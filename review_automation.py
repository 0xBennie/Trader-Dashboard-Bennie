"""Persistent review work, independent of exchange connectivity and browser sessions.

Daily summaries are deterministic evidence summaries, never labelled AI reports.
The optional AI queue uses the user's configured provider and bounded retries.
"""
import json
import threading
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import ai_review
import journal
import review_learning

HK = ZoneInfo('Asia/Hong_Kong')
QUEUE_LOCK = threading.Lock()


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS daily_reviews (
        day TEXT PRIMARY KEY, cutoff INTEGER NOT NULL, created INTEGER NOT NULL, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS review_job_meta (
        event_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, last_attempt INTEGER);
    CREATE TABLE IF NOT EXISTS browser_observations (
        id TEXT PRIMARY KEY, journal_id TEXT NOT NULL, observed_at INTEGER NOT NULL, data TEXT NOT NULL);
    ''')


def period(now):
    local = datetime.fromtimestamp(now/1000, HK)
    cutoff = local.replace(hour=21, minute=0, second=0, microsecond=0)
    if cutoff > local:
        cutoff -= timedelta(days=1)
    end = int(cutoff.timestamp()*1000)
    return end-86400000, end


def result(record):
    """Do not turn absent costs into zero, or a screenshot into settled P/L."""
    if record.get('exchange') == 'variational':
        c = record.get('browser_close') or {}
        return dict(realized=c.get('realized_pnl_display'), asset=c.get('realized_pnl_asset'),
                    net=None, note='页面已实现盈亏；手续费、资金费与净额未完成对账')
    c = record.get('execution') or {}
    net = None
    try:
        fees = c.get('fees')
        if c.get('history_complete') is True and isinstance(fees, dict) and set(fees) <= {'USDT'}:
            amount = Decimal(c['realized_pnl'])-Decimal(fees.get('USDT', '0'))
            if amount.is_finite():
                net = str(amount)
    except (KeyError, InvalidOperation, TypeError):
        pass
    return dict(realized=c.get('realized_pnl'), asset='USDT', net=net,
                note='已扣成交手续费，资金费未按轮次分摊；不是账户净收益')


def item(record):
    closed = review_learning.closed(record)
    return dict(id=record['id'], symbol=record['symbol'], exchange=record.get('exchange', 'binance'),
                direction=record.get('direction'), status=record.get('status'),
                opened_at=review_learning.opened(record), closed_at=closed,
                entry_price=record.get('entry_price'), quantity=record.get('quantity'),
                thesis=record.get('thesis', ''), stop_loss=record.get('stop_loss', ''),
                take_profit=record.get('take_profit', ''),
                result=result(record) if closed else None,
                personal_complete=record.get('review_status', {}).get('personal_complete', False),
                source='浏览器核对' if record.get('browser_close') or record.get('browser_observation') else
                       '交易所 API' if record.get('execution') else '截图 / 手动记录')


def build(db, start, end, generated):
    records = journal.all_journals(db)
    journal.review_progress(db, records, generated)
    closed = [r for r in records if (review_learning.closed(r) or 0) > start
              and review_learning.closed(r) <= end]
    recorded = [r for r in records if start < r['created'] <= end]
    unknown = [r for r in records if r.get('status') == '已平仓' and not review_learning.closed(r)]
    # These are observations at generation time, not a reconstructed 21:00 position ledger.
    holdings = [r for r in records if r.get('status') == '持仓中']
    pending = [r for r in records if r.get('review_status', {}).get('eligible') and
               not r['review_status']['personal_complete']]
    return dict(start=start, through=end, generated_at=generated, timezone='Asia/Hong_Kong',
                closed=[item(r) for r in closed], recorded=[item(r) for r in recorded],
                holdings=[item(r) for r in holdings], needs_verification=[item(r) for r in unknown],
                pending=[item(r) for r in pending],
                missing_reason=[item(r) for r in recorded if not str(r.get('thesis') or '').strip()],
                scope='前一日 21:00 至当日 21:00（香港）；按已知平仓时间归属。新建日志不等于当日新开仓。',
                holdings_scope='生成时日志中最近一次观察的持仓；截图与过期快照不代表当前仓位。',
                kind='evidence_summary')


def ensure_daily(now=None):
    now = journal.now_ms() if now is None else now
    start, end = period(now)
    day = datetime.fromtimestamp(end/1000, HK).date().isoformat()
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT data FROM daily_reviews WHERE day=?', (day,)).fetchone()
        if previous:
            return json.loads(previous[0])
        report = dict(build(db, start, end, now), day=day)
        db.execute('INSERT INTO daily_reviews VALUES(?,?,?,?)', (day, end, now, json.dumps(report, ensure_ascii=False)))
        return report


def enqueue(db, now):
    added = 0
    for record in journal.all_journals(db):
        closed = review_learning.closed(record)
        if record.get('status') == '计划中' or (record.get('status') == '已平仓' and not closed):
            continue
        since = closed or record['created']
        if not 0 <= now-since <= 30*86400000:
            continue
        if not (record.get('execution') or record.get('screenshot_snapshot') or record.get('browser_observation')):
            continue
        if db.execute("SELECT 1 FROM analyses WHERE journal_id=? AND status='complete' AND created>=?",
                      (record['id'], since)).fetchone():
            continue
        # Reuse recorder-created jobs; no duplicate provider call for the same transition.
        if db.execute("SELECT 1 FROM auto_reviews WHERE journal_id=? AND status IN ('pending','running')", (record['id'],)).fetchone():
            continue
        key = 'journal:'+record['id']+(':close:'+str(closed) if closed else ':entry')
        added += db.execute('INSERT OR IGNORE INTO auto_reviews VALUES(?,?,?,?,NULL)',
                            (key, record['id'], now+30000, 'pending')).rowcount
    return added


def _failure(db, key, now):
    row = db.execute('SELECT attempts FROM review_job_meta WHERE event_id=?', (key,)).fetchone()
    attempts = row[0] if row else 1
    db.execute('INSERT OR IGNORE INTO review_job_meta VALUES(?,?,?,?)', (key, attempts, None, now))
    db.execute('UPDATE review_job_meta SET last_error=? WHERE event_id=?',
               ('生成未完成；原始证据保留。请检查 AI 连接后重试。', key))
    db.execute('UPDATE auto_reviews SET status=?,due=? WHERE event_id=?',
               ('failed' if attempts >= 3 else 'pending', now+min(1800000, 60000*5**attempts), key))


def process_queue(now=None):
    now = journal.now_ms() if now is None else now
    if not QUEUE_LOCK.acquire(blocking=False):
        return
    try:
        with journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM auto_reviews WHERE status='running'").fetchall():
                report = db.execute('SELECT status FROM analyses WHERE id=?', (row['analysis_id'],)).fetchone()
                if report and report[0] == 'complete':
                    db.execute("UPDATE auto_reviews SET status='complete' WHERE event_id=?", (row['event_id'],))
                elif not report or report[0] != 'running':
                    _failure(db, row['event_id'], now)
        if not journal.kv('recorder_settings', {}).get('auto_ai', True) or not ai_review.status()['available'] or ai_review.LOCK.locked():
            return
        with journal.connect() as db:
            row = db.execute("SELECT * FROM auto_reviews WHERE status='pending' AND due<=? ORDER BY due,event_id LIMIT 1", (now,)).fetchone()
            if not row:
                return
            db.execute('INSERT OR IGNORE INTO review_job_meta VALUES(?,0,NULL,NULL)', (row['event_id'],))
            db.execute('UPDATE review_job_meta SET attempts=attempts+1,last_attempt=?,last_error=NULL WHERE event_id=?', (now,row['event_id']))
        try:
            aid = ai_review.start(row['journal_id'])
            with journal.connect() as db:
                db.execute("UPDATE auto_reviews SET status='running',analysis_id=? WHERE event_id=?", (aid,row['event_id']))
        except Exception:
            # Never persist arbitrary transport exceptions containing headers or URLs.
            with journal.connect() as db:
                _failure(db, row['event_id'], now)
    finally:
        QUEUE_LOCK.release()


def status(db, now=None):
    now = journal.now_ms() if now is None else now
    latest = db.execute('SELECT data FROM daily_reviews ORDER BY cutoff DESC LIMIT 1').fetchone()
    counts = {r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM auto_reviews GROUP BY status')}
    ai = ai_review.status()
    worker = journal.kv('review_worker', {}, db)
    return dict(schedule='21:00', timezone='Asia/Hong_Kong', next_at=period(now)[1]+86400000,
                queue=counts, ai_state='paused' if not journal.kv('recorder_settings', {},db).get('auto_ai',True) else
                'ready' if ai['available'] else 'not_configured',
                worker=worker, healthy=bool(not worker.get('error') and worker.get('last_success') and 0<=now-worker['last_success']<120000),
                latest=json.loads(latest[0]) if latest else None,
                browser=journal.kv('variational_browser_status', {'status':'not_connected'}, db))


def listing():
    with journal.connect() as db:
        return dict(status(db), reports=[json.loads(r[0]) for r in db.execute('SELECT data FROM daily_reviews ORDER BY cutoff DESC LIMIT 30')])


def retry_failed():
    with journal.connect() as db:
        keys = [r[0] for r in db.execute("SELECT event_id FROM auto_reviews WHERE status='failed'")]
        db.execute("UPDATE auto_reviews SET status='pending',due=? WHERE status='failed'", (journal.now_ms(),))
        db.executemany('UPDATE review_job_meta SET attempts=0,last_error=NULL WHERE event_id=?', [(k,) for k in keys])
        return {'queued':len(keys)}


class Worker:
    def __init__(self):
        self.stop = threading.Event()

    def tick(self):
        now = journal.now_ms()
        ensure_daily(now)
        with journal.connect() as db:
            enqueue(db, now)
        process_queue(now)
        with journal.connect() as db:
            journal.set_kv(db, 'review_worker', {'last_success':now,'error':None})

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                with journal.connect() as db:
                    old = journal.kv('review_worker', {}, db)
                    journal.set_kv(db, 'review_worker', dict(old, attempted_at=journal.now_ms(), error='自动复盘暂未完成，将重试；已有记录保留。'))
            self.stop.wait(30)

    def start(self):
        threading.Thread(target=self.run, name='review-worker', daemon=True).start()
        return self
