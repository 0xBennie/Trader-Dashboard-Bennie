"""User-authored review patterns and traceable practice; never infer mistakes from P/L."""
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

MISTAKES = {
    'impulse': '临时起意', 'chasing': '追涨追跌', 'size': '仓位超计划',
    'stop': '放宽止损', 'early_exit': '提前离场', 'exit': '未按退出条件执行',
    'protection': '未执行盈利保护', 'repeat': '重复追单',
}
EXECUTION = {'': '未评价', 'followed': '按计划执行', 'deviated': '偏离计划', 'no_plan': '没有明确计划'}
TEXT_FIELDS = ('reflection', 'review_keep', 'review_next')


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS review_actions (
        id TEXT PRIMARY KEY, journal_id TEXT NOT NULL, instruction TEXT NOT NULL,
        created INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
        UNIQUE(journal_id,instruction));
    CREATE TABLE IF NOT EXISTS review_practices (
        id TEXT PRIMARY KEY, action_id TEXT NOT NULL, journal_id TEXT NOT NULL,
        outcome TEXT NOT NULL, note TEXT NOT NULL, created INTEGER NOT NULL,
        voided INTEGER NOT NULL DEFAULT 0);
    CREATE UNIQUE INDEX IF NOT EXISTS review_practice_active
        ON review_practices(action_id,journal_id) WHERE voided=0;
    ''')


def content(record):
    return tuple(str(record.get(key) or '').strip() for key in TEXT_FIELDS)


def fields(payload, previous):
    result = {}
    for key in ('review_keep', 'review_next'):
        value = payload.get(key, previous.get(key, ''))
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError('复盘内容请使用不超过 2000 字的文字。')
        result[key] = value.strip()
    execution = payload.get('review_execution', previous.get('review_execution', ''))
    if not isinstance(execution, str) or execution not in EXECUTION:
        raise ValueError('请选择有效的执行评价。')
    raw = payload.get('review_mistakes', previous.get('review_mistakes', ''))
    if not isinstance(raw, str) or len(raw) > 200:
        raise ValueError('复盘问题格式不正确。')
    tags = set(filter(None, raw.split(',')))
    if tags - MISTAKES.keys():
        raise ValueError('复盘问题包含不支持的标签。')
    result.update(review_execution=execution, review_mistakes=','.join(sorted(tags)))
    return result


def opened(record):
    value = (record.get('execution') or {}).get('opened_ms')
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    try:
        stamp = datetime.fromisoformat(record.get('opened_at') or '')
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo('Asia/Hong_Kong'))
        return int(stamp.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def practice_eligible(record, action, now):
    start = opened(record)
    closed = (record.get('execution') or {}).get('closed_ms')
    ended = bool(isinstance(closed, int) and not isinstance(closed, bool) and start is not None and start <= closed <= now) if closed is not None else record.get('status') == '已平仓'
    return record['id'] != action['journal_id'] and ended and start is not None and action['created'] <= start <= now


def _id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError('记录编号格式不正确。')
    return value


def _record(db, jid):
    jid = _id(jid)
    row = db.execute('SELECT * FROM journals WHERE id=?', (jid,)).fetchone()
    if row is None:
        raise ValueError('找不到这笔交易。')
    return dict(json.loads(row['data']), id=row['id'], created=row['created'], updated=row['updated'])


def create_action(payload):
    import journal
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        source = _record(db, payload.get('journal_id'))
        text = str(source.get('review_next') or '').strip()
        if not text:
            raise ValueError('先在复盘中写下并保存「下一笔只改一件事」。')
        existing = db.execute('SELECT * FROM review_actions WHERE journal_id=? AND instruction=?', (source['id'], text)).fetchone()
        if existing:
            db.execute('UPDATE review_actions SET archived=0 WHERE id=?', (existing['id'],))
            return dict(existing, archived=0)
        item = dict(id=uuid.uuid4().hex, journal_id=source['id'], instruction=text, created=journal.now_ms(), archived=0)
        db.execute('INSERT INTO review_actions VALUES(:id,:journal_id,:instruction,:created,:archived)', item)
        return item


def archive_action(payload):
    import journal
    if not isinstance(payload.get('archived'), bool):
        raise ValueError('提醒状态格式不正确。')
    with journal.connect() as db:
        if not db.execute('SELECT id FROM review_actions WHERE id=?', (_id(payload.get('id')),)).fetchone():
            raise ValueError('找不到这条提醒。')
        db.execute('UPDATE review_actions SET archived=? WHERE id=?', (int(payload['archived']), payload['id']))
    return {'ok': True}


def practice(payload):
    import journal
    outcome, note = payload.get('outcome'), payload.get('note', '')
    if not isinstance(outcome, str) or outcome not in {'followed', 'missed'}:
        raise ValueError('请选择「做到了」或「还没做到」。')
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
        raise ValueError('请用一句话说明这笔交易中实际做了什么（最多 2000 字）。')
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM review_actions WHERE id=?', (_id(payload.get('action_id')),)).fetchone()
        if row is None or row['archived']:
            raise ValueError('这条提醒已收起或不存在，请先恢复。')
        record = _record(db, payload.get('journal_id'))
        now = journal.now_ms()
        if not practice_eligible(record, dict(row), now):
            raise ValueError('请选择提醒创建后入场、且已平仓的另一笔交易；入场时间需已填写。')
        existing = db.execute('SELECT * FROM review_practices WHERE action_id=? AND journal_id=? AND voided=0', (row['id'], record['id'])).fetchone()
        if existing:
            if existing['outcome'] == outcome and existing['note'] == note.strip():
                return dict(existing)
            raise ValueError('这笔交易已有实践记录。如需更正，请先撤销原记录。')
        item = dict(id=uuid.uuid4().hex, action_id=row['id'], journal_id=record['id'], outcome=outcome, note=note.strip(), created=now, voided=0)
        db.execute('INSERT INTO review_practices VALUES(:id,:action_id,:journal_id,:outcome,:note,:created,:voided)', item)
        return item


def undo_practice(payload):
    import journal
    with journal.connect() as db:
        if not db.execute('SELECT id FROM review_practices WHERE id=?', (_id(payload.get('id')),)).fetchone():
            raise ValueError('找不到这条实践记录。')
        db.execute('UPDATE review_practices SET voided=1 WHERE id=?', (payload['id'],))
    return {'ok': True}


def summary(db, records, now, days=30, exchange='all'):
    if isinstance(days, bool) or days not in (30, 90) or exchange not in ('all', 'binance', 'okx', 'bybit', 'variational'):
        raise ValueError('请选择近 30／90 天及有效的交易所。')
    start = now - days * 86400000
    by_id = {j['id']: j for j in records}
    matching = [j for j in records if exchange == 'all' or j.get('exchange', 'binance') == exchange]
    closed = [j for j in matching if isinstance((j.get('execution') or {}).get('closed_ms'), int)
              and start <= j['execution']['closed_ms'] <= now]
    reviewed = [j for j in closed if j.get('review_status', {}).get('personal_complete')]
    structured = [j for j in reviewed if j.get('review_execution') or j.get('review_mistakes') or j.get('review_keep') or j.get('review_next')]
    examples = lambda rows: [dict(id=j['id'], symbol=j['symbol'], exchange=j.get('exchange', 'binance'), closed_at=j['execution']['closed_ms']) for j in sorted(rows, key=lambda j:j['execution']['closed_ms'], reverse=True)]
    patterns = []
    for key, label in MISTAKES.items():
        rows = [j for j in structured if key in set(str(j.get('review_mistakes') or '').split(','))]
        if rows:
            patterns.append(dict(key=key, label=label, count=len(rows), journals=examples(rows)))
    patterns.sort(key=lambda x:(-x['count'], x['key']))
    actions = []
    for row in db.execute('SELECT * FROM review_actions ORDER BY created DESC,id'):
        source = by_id.get(row['journal_id'])
        if not source or (exchange != 'all' and source.get('exchange', 'binance') != exchange):
            continue
        item = dict(row, symbol=source['symbol'], exchange=source.get('exchange', 'binance'))
        practices = [dict(p) for p in db.execute('SELECT * FROM review_practices WHERE action_id=? AND voided=0 ORDER BY created DESC,id', (row['id'],))]
        used = {p['journal_id'] for p in practices}
        item['practices'] = [dict(p, symbol=by_id.get(p['journal_id'], {}).get('symbol', '已归档交易')) for p in practices]
        item['eligible'] = [dict(id=j['id'],symbol=j['symbol'],exchange=j.get('exchange','binance'),opened_at=opened(j))
                            for j in records if j['id'] not in used and practice_eligible(j,item,now)] if not item['archived'] else []
        actions.append(item)
    return dict(days=days, exchange=exchange, start=start, through=now, closed_count=len(closed), reviewed_count=len(reviewed),
                structured_count=len(structured), unknown_close_count=sum(j.get('status')=='已平仓' and not (j.get('execution') or {}).get('closed_ms') for j in matching),
                execution=[dict(key=k,label=v,count=sum(j.get('review_execution')==k for j in structured)) for k,v in EXECUTION.items() if k],
                patterns=patterns, actions=actions, unstructured=examples([j for j in reviewed if j not in structured]))


def report(days=30, exchange='all'):
    import journal
    with journal.connect() as db:
        db.execute('BEGIN')
        records = journal.all_journals(db)
        now = journal.now_ms()
        journal.review_progress(db, records, now)
        return summary(db, records, now, days, exchange)
