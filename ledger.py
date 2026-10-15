"""Exact, shared accounting rules. Unknown money never becomes a silent zero."""
from decimal import Decimal, InvalidOperation, localcontext
from functools import wraps

VERSION = 2
KINDS = {'REALIZED_PNL': 'realized', 'COMMISSION': 'fees', 'FUNDING_FEE': 'funding',
         'COMMISSION_REBATE': 'fees', 'API_REBATE': 'fees', 'INSURANCE_CLEAR': 'realized'}
NON_TRADING = {'TRANSFER', 'INTERNAL_TRANSFER', 'WELCOME_BONUS', 'REFERRAL_KICKBACK',
               'DEPOSIT', 'WITHDRAW', 'BONUS', 'BONUS_RECOLLECT'}


def exact(function):
    @wraps(function)
    def call(*args, **kwargs):
        with localcontext() as ctx:
            ctx.prec = 100
            return function(*args, **kwargs)
    return call


def amount(value):
    try:
        if isinstance(value, (bool, float)) or value is None or len(str(value)) > 100:
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or result.adjusted() > 40 or result.as_tuple().exponent < -30:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('账单金额无效，停止汇总，需重新同步核对。') from None


def classified(rows, allow_unknown=False):
    result, seen = [], {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError('账单格式无效。')
        try:
            kind, asset, stamp, value = raw['incomeType'], raw['asset'], raw['time'], amount(raw['income'])
            symbol = raw.get('symbol') or ''
            if not isinstance(kind, str) or not isinstance(asset, str) or not asset or not isinstance(symbol, str) or type(stamp) is not int or stamp < 0:
                raise ValueError('账单必要字段无效。')
            # Aggregate helpers accept old exports without IDs; persisted reads validate IDs separately.
            if 'tranId' in raw:
                ident = raw['tranId']
                if type(ident) not in (str, int) or not str(ident).strip():
                    raise ValueError('账单编号无效。')
                key = (raw.get('exchange', 'binance'), kind, str(ident))
                signature = (stamp, asset, value, symbol)
                if key in seen:
                    if seen[key] != signature:
                        raise ValueError('同一账单编号存在冲突，停止汇总。')
                    continue
                seen[key] = signature
            if kind in NON_TRADING or (symbol and not symbol.endswith('USDT')):
                continue
            if kind not in KINDS:
                if not allow_unknown and (value or raw.get('unclassified')):
                    raise ValueError('存在未分类账单，净收益待核对。')
                continue
            result.append(raw)
        except (KeyError, TypeError):
            raise ValueError('账单必要字段缺失。') from None
    return result


def record_coverage(db, venue, start, end):
    """Only call after a successful complete, identity-bound ledger read."""
    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        raise ValueError('账单覆盖时段无效。')
    rows = db.execute('SELECT start,end FROM ledger_coverage WHERE exchange=? AND end>=? AND start<=?', (venue, start-1, end+1)).fetchall()
    if rows:
        start, end = min(start, *(r[0] for r in rows)), max(end, *(r[1] for r in rows))
        db.executemany('DELETE FROM ledger_coverage WHERE exchange=? AND start=?', [(venue, r[0]) for r in rows])
    db.execute('INSERT OR REPLACE INTO ledger_coverage VALUES(?,?,?)', (venue, start, end))


def ranges(db, venue):
    return [list(row) for row in db.execute('SELECT start,end FROM ledger_coverage WHERE exchange=? ORDER BY start', (venue,))]


def verified_coverage(db, venue):
    values = ranges(db, venue)
    if not values:
        return {"complete": False}
    start, end = values[-1]
    return {"start": start, "end": end, "complete": True, "ledger_version": VERSION}
