"""Explicit, immutable sharing of allowlisted reports. No raw account state leaves here."""
import calendar
import json
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import journal
import ledger

HK = ZoneInfo('Asia/Hong_Kong')
VENUES = {'binance': 'Binance', 'okx': 'OKX', 'bybit': 'Bybit', 'variational': 'Variational'}
DAY = 86400000


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS share_coverage (exchange TEXT, start INTEGER, end INTEGER, PRIMARY KEY(exchange,start));
    CREATE TABLE IF NOT EXISTS shared_reports (
      id TEXT PRIMARY KEY, token TEXT UNIQUE, created INTEGER NOT NULL, published INTEGER,
      expires INTEGER, revoked INTEGER, report TEXT NOT NULL);
    ''')
    for venue in ('binance', 'okx', 'bybit'):
        snapshot = journal.kv('snapshot' if venue == 'binance' else f'exchange:{venue}:snapshot', {}, db)
        record_coverage(db, venue, snapshot)


def record_coverage(db, venue, snapshot):
    c = snapshot.get('coverage', {})
    if not c.get('complete') or not isinstance(c.get('start'), int) or not isinstance(c.get('end'), int):
        return
    start, end = c['start'], c['end']
    if c.get('ledger_version') == ledger.VERSION:
        ledger.record_coverage(db, venue, start, end)
    if start < 0 or end < start:
        return
    rows = db.execute('SELECT start,end FROM share_coverage WHERE exchange=? AND end>=? AND start<=?',
                      (venue, start - 1, end + 1)).fetchall()
    if rows:
        start, end = min(start, *(r[0] for r in rows)), max(end, *(r[1] for r in rows))
        db.executemany('DELETE FROM share_coverage WHERE exchange=? AND start=?', [(venue, r[0]) for r in rows])
    db.execute('INSERT OR REPLACE INTO share_coverage VALUES(?,?,?)', (venue, start, end))


def decimal(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def date_key(stamp):
    return datetime.fromtimestamp(stamp / 1000, HK).strftime('%Y-%m-%d')


def text(value, limit):
    if not isinstance(value, str):
        raise ValueError('文字格式不正确。')
    value = ''.join(c for c in value if c in '\n\t' or ord(c) >= 32).strip()
    if len(value) > limit:
        raise ValueError(f'文字最多 {limit} 个字符。')
    return value


def config(payload, stamp):
    mode, venue = payload.get('mode', 'month'), payload.get('exchange', 'binance')
    if mode not in ('year', 'month', 'trade') or venue not in VENUES:
        raise ValueError('请选择有效的报告类型与交易所。')
    result = {'mode': mode, 'exchange': venue, 'nickname': text(payload.get('nickname', 'Trader'), 32) or 'Trader',
              'note': text(payload.get('note', ''), 180)}
    for key in ('show_amounts', 'show_details', 'show_note'):
        value = payload.get(key, False)
        if not isinstance(value, bool):
            raise ValueError('展示开关格式不正确。')
        result[key] = value
    result['metric'] = payload.get('metric', 'reviews')
    if result['metric'] not in ('reviews', 'pnl'):
        raise ValueError('请选择复盘活动或每日盈亏。')
    if mode == 'trade':
        result['journal_id'] = text(payload.get('journal_id', ''), 100)
        if not result['journal_id']:
            raise ValueError('请先选择一笔交易。')
    else:
        period = payload.get('period', date_key(stamp)[:4 if mode == 'year' else 7])
        if not isinstance(period, str) or not re.fullmatch(r'20\d{2}' if mode == 'year' else r'20\d{2}-(0[1-9]|1[0-2])', period):
            raise ValueError('日期格式不正确。')
        if period > date_key(stamp)[:len(period)]:
            raise ValueError('不能生成未来期间的报告。')
        result['period'] = period
    return result


def first_review(db, item):
    closed = (item.get('execution') or {}).get('closed_ms')
    import review_learning
    if not closed or not any(review_learning.content(item)):
        return None
    versions = [(r[0], json.loads(r[1])) for r in db.execute(
        'SELECT time,data FROM revisions WHERE journal_id=? ORDER BY id', (item['id'],))]
    versions.append((item['updated'], item))
    previous = ('', '', '')
    for stamp, version in versions:
        value = review_learning.content(version)
        if any(text and text != old for text, old in zip(value, previous)) and stamp >= closed:
            return stamp
        previous = value
    return None


def complete(item):
    e = item.get('execution') or {}
    return item.get('exchange', 'binance') != 'variational' and e.get('history_complete') is True and bool(e.get('closed_ms'))


def trade_detail(item, cfg):
    e = item.get('execution') or {}
    result = {'symbol': e.get('symbol') or item['symbol'], 'direction': e.get('direction') or item.get('direction', 'UNKNOWN'),
              'status': '已平仓' if e.get('closed_ms') else item.get('status', '待核对'),
              'opened': date_key(e['opened_ms']) if e.get('opened_ms') else None,
              'closed': date_key(e['closed_ms']) if e.get('closed_ms') else None,
              'source': '完整成交轮次' if complete(item) else '成交已关联 · 持仓中' if e.get('history_complete') and e.get('opened_ms') else '手动 / 不完整记录'}
    # Prices and quantities are also omitted when monetary visibility is off.
    if cfg['show_amounts']:
        for name, value in [('entry_price', e.get('entry_price') or item.get('entry_price')),
                            ('quantity', e.get('entry_quantity') or item.get('quantity'))]:
            if (n := decimal(value)) is not None:
                result[name] = str(n)
        if complete(item):
            result['gross_pnl'] = str(decimal(e.get('realized_pnl')) or 0)
            result['fees_usdt'] = str(decimal(e.get('fees', {}).get('USDT', '0')) or 0)
            result['other_fee_assets'] = sorted(k for k in e.get('fees', {}) if k != 'USDT')
            exits = [x for x in e.get('events', []) if x.get('kind') in ('close', 'reduce')]
            qty = sum((decimal(x.get('quantity')) or 0 for x in exits), Decimal(0))
            if qty:
                result['exit_price'] = str(sum((decimal(x.get('price')) or 0) * (decimal(x.get('quantity')) or 0) for x in exits) / qty)
    return result


@ledger.exact
def build_report(db, payload, stamp):
    cfg = config(payload, stamp)
    venue = cfg['exchange']
    records = [r for r in journal.all_journals(db) if r.get('exchange', 'binance') == venue]
    # A cycle is counted once even if an older manual journal references the same execution.
    unique = {}
    for r in records:
        unique.setdefault((r.get('execution') or {}).get('cycle_id') or r['id'], r)
    records = list(unique.values())
    report = {'version': 1, 'mode': cfg['mode'], 'nickname': cfg['nickname'], 'venue': VENUES[venue],
              'timezone': 'Asia/Hong_Kong', 'asset': 'USD' if venue == 'variational' else 'USDT',
              'generated_at': stamp, 'show_amounts': cfg['show_amounts'], 'show_details': cfg['show_details'],
              'metric': cfg['metric'], 'note': cfg['note'] if cfg['show_note'] else '',
              'source': '手动记录，未完整对账' if venue == 'variational' else '交易所 API 账单与本地日志',
              'method': '已实现净额含手续费与资金费，不含浮盈亏或划转；非账户收益率。'}
    if cfg['mode'] == 'trade':
        item = next((r for r in records if r['id'] == cfg['journal_id']), None)
        if item is None:
            raise ValueError('找不到所选交易所的这条交易。')
        # Trade identity is an explicit choice in the single-trade template. Never copy arbitrary notes.
        report.update(period=date_key(stamp)[:4], title='交易手记', trade=trade_detail(item, cfg), days=[], bars=[], details=[])
        report['method'] = '单笔盈亏显示成交平仓盈亏与手续费；未分配资金费。手动价格不是交易所对账结果。'
        if report['trade'].get('other_fee_assets'):
            report['method'] += '另有 ' + '、'.join(report['trade'].pop('other_fee_assets')) + ' 手续费未折算。'
        else:
            report['trade'].pop('other_fee_assets', None)
        return report
    period = cfg['period']
    year = int(period[:4]); month = int(period[5:7]) if cfg['mode'] == 'month' else 1
    start = datetime(year, month, 1, tzinfo=HK)
    end = datetime(year + 1, 1, 1, tzinfo=HK) if cfg['mode'] == 'year' else start.replace(day=calendar.monthrange(year, month)[1]) + timedelta(days=1)
    start_ms, stop_ms = int(start.timestamp() * 1000), min(int(end.timestamp() * 1000) - 1, stamp)
    ranges = [(max(start_ms, r[0]), min(stop_ms, r[1])) for r in db.execute('SELECT start,end FROM ledger_coverage WHERE exchange=? ORDER BY start', (venue,)) if r[1] >= start_ms and r[0] <= stop_ms]
    days = []
    for i in range((end - start).days):
        day = start + timedelta(days=i); lo = int(day.timestamp() * 1000); hi = lo + DAY - 1
        covered = sum(max(0, min(b, hi) - max(a, lo) + 1) for a, b in ranges)
        status = 'future' if lo > stamp else 'full' if covered == DAY else 'partial' if covered else 'unknown'
        days.append({'date': day.strftime('%Y-%m-%d'), 'coverage': status, 'reviews': 0, 'closed': 0, '_net': Decimal(0), '_ledger': 0})
    daily = {r['date']: r for r in days}
    covered_time = lambda t: any(a <= t <= b for a, b in ranges)
    income = []
    if venue == 'binance':
        income = [json.loads(r[0]) for r in db.execute('SELECT raw FROM income WHERE time BETWEEN ? AND ?', (start_ms, stop_ms))]
    elif venue in ('okx', 'bybit'):
        income = [json.loads(r[0]) for r in db.execute('SELECT raw FROM exchange_income WHERE exchange=? AND time BETWEEN ? AND ?', (venue, start_ms, stop_ms))]
    income = ledger.classified([r for r in income if covered_time(r['time'])])
    for entry in income:
        if entry.get('asset') != 'USDT' or not covered_time(entry['time']):
            continue
        value = decimal(entry['income'])
        if value is not None:
            daily[date_key(entry['time'])]['_net'] += value
            daily[date_key(entry['time'])]['_ledger'] += 1
    cohort, excluded, review_times = [], 0, {}
    for item in records:
        e = item.get('execution') or {}; closed = e.get('closed_ms')
        if closed and start_ms <= closed <= stop_ms:
            if complete(item):
                cohort.append(item); daily[date_key(closed)]['closed'] += 1
            else:
                excluded += 1
        review = first_review(db, item)
        review_times[item['id']] = review
        if review and start_ms <= review <= stop_ms:
            daily[date_key(review)]['reviews'] += 1
    reviewed = sum(bool((r := review_times[item['id']]) and r <= stop_ms) for item in cohort)
    bars = []
    for group in ([d['date'][:7] for d in days] if cfg['mode'] == 'year' else [d['date'] for d in days]):
        if bars and bars[-1]['key'] == group:
            continue
        members = [d for d in days if d['date'].startswith(group)]
        known = [d for d in members if d['coverage'] in ('full', 'partial')]
        value = sum((d['_net'] for d in known), Decimal(0))
        bar = {'key': group, 'known': bool(known), 'partial': any(d['coverage'] != 'full' for d in members), 'sign': (value > 0) - (value < 0)}
        if cfg['show_amounts'] and known:
            bar['net'] = str(value)
        bars.append(bar)
    for day in days:
        value = day.pop('_net'); day.pop('_ledger')
        if day['coverage'] in ('full', 'partial'):
            day['sign'] = (value > 0) - (value < 0)
            if cfg['show_amounts']:
                day['net'] = str(value)
    report.update(period=period, title='交易年记' if cfg['mode'] == 'year' else '交易月记',
                  start=date_key(start_ms), through=date_key(stop_ms), days=days, bars=bars,
                  coverage=[{'start': date_key(a), 'end': date_key(b)} for a, b in ranges],
                  summary={'closed': len(cohort), 'reviewed': reviewed, 'review_days': sum(d['reviews'] > 0 for d in days),
                           'excluded': excluded, 'covered_days': sum(d['coverage'] in ('full', 'partial') for d in days)},
                  details=[{k: v for k, v in trade_detail(item, cfg).items() if k in ('symbol', 'direction', 'closed', 'gross_pnl')}
                           for item in sorted(cohort, key=lambda j: j['execution']['closed_ms'], reverse=True)[:8]] if cfg['show_details'] else [])
    if cfg['show_amounts'] and ranges:
        report['net'] = str(sum((Decimal(b['net']) for b in bars if 'net' in b), Decimal(0)))
    return report


def preview(payload):
    stamp = journal.now_ms()
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Drafts never have public tokens; expired report data is removed rather than archived forever.
        db.execute('DELETE FROM shared_reports WHERE published IS NULL AND created<?', (stamp - DAY,))
        db.execute('DELETE FROM shared_reports WHERE id IN (SELECT id FROM shared_reports WHERE published IS NULL ORDER BY created DESC LIMIT -1 OFFSET 99)')
        db.execute("UPDATE shared_reports SET report='{}' WHERE expires<? OR revoked IS NOT NULL", (stamp,))
        report = build_report(db, payload, stamp)
        draft = secrets.token_urlsafe(18)
        db.execute('INSERT INTO shared_reports(id,created,report) VALUES(?,?,?)', (draft, stamp, json.dumps(report, ensure_ascii=False)))
    return {'draft_id': draft, 'report': report}


def publish(payload):
    if payload.get('confirmed') is not True:
        raise ValueError('请确认发布当前预览，持有链接的人可查看所选内容。')
    ttl = payload.get('days', 30)
    if type(ttl) is not int or ttl not in (7, 30, 90):
        raise ValueError('链接有效期可选 7、30 或 90 天。')
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM shared_reports WHERE id=?', (str(payload.get('draft_id', '')),)).fetchone()
        stamp = journal.now_ms()
        if not row or row['revoked'] or (row['expires'] and row['expires'] <= stamp):
            raise ValueError('预览不存在或链接已失效，请重新生成。')
        if not row['published']:
            if row['created'] < stamp - DAY:
                raise ValueError('预览已过期，请刷新报告后发布。')
            count = db.execute('SELECT COUNT(*) FROM shared_reports WHERE revoked IS NULL AND expires>?', (stamp,)).fetchone()[0]
            if count >= 200:
                raise ValueError('有效分享链接已达 200 条，请先撤销不再使用的链接。')
            token, expires = secrets.token_urlsafe(32), stamp + ttl * DAY
            db.execute('UPDATE shared_reports SET token=?,published=?,expires=? WHERE id=?', (token, stamp, expires, row['id']))
        else:
            token, expires = row['token'], row['expires']
    return {'id': row['id'], 'path': '/s/' + token, 'expires': expires}


def listing():
    with journal.connect() as db:
        rows = db.execute('SELECT * FROM shared_reports WHERE published IS NOT NULL ORDER BY (revoked IS NULL AND expires>?) DESC,published DESC LIMIT 200', (journal.now_ms(),)).fetchall()
    return {'items': [{'id': r['id'], 'path': '/s/' + r['token'], 'published': r['published'], 'expires': r['expires'],
                       'revoked': bool(r['revoked']), 'active': not r['revoked'] and r['expires'] > journal.now_ms(),
                       'title': json.loads(r['report']).get('title', '已失效的报告')} for r in rows]}


def revoke(payload):
    with journal.connect() as db:
        cursor = db.execute("UPDATE shared_reports SET revoked=?,report='{}' WHERE id=? AND published IS NOT NULL", (journal.now_ms(), str(payload.get('id', ''))))
        if not cursor.rowcount:
            raise ValueError('分享链接不存在。')
    return {'revoked': True}


def public_report(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return None
    with journal.connect() as db:
        row = db.execute('SELECT report FROM shared_reports WHERE token=? AND revoked IS NULL AND expires>?', (token, journal.now_ms())).fetchone()
    return json.loads(row[0]) if row else None
