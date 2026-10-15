"""A common-period, currency-separated ledger for all connected exchanges.

Only persisted exchange income is money evidence. Fills are counts, never a
second source of profit/fees. One SQLite read transaction supplies each report.
"""
import json
from decimal import Decimal, localcontext

from analytics import build_analytics

DAY = 86400000
VENUES = {'binance': 'Binance', 'okx': 'OKX', 'bybit': 'Bybit'}
from ledger import KINDS, NON_TRADING, amount, exact, ranges


def merge(ranges):
    merged = []
    for start, end in sorted(ranges):
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def common_window(accounts, start, end):
    common = [[start, end]]
    for account in accounts:
        common = merge([(max(a, x), min(b, y)) for a, b in common
                        for x, y in merge(account.get('ranges', [])) if max(a, x) <= min(b, y)])
    return common[-1] if common and accounts else None


def bucket():
    return {key: Decimal(0) for key in ('realized', 'fees', 'funding', 'net')}


def export(values):
    return {asset: {key: str(value) for key, value in items.items()} for asset, items in sorted(values.items())}


@exact
def build(accounts, start, end, now, excluded=None):
    """Latest shared continuous interval, never a sum of unrelated date windows."""
    result = {'available': False, 'requested': {'start': start, 'end': end},
              'window': None, 'accounts': [], 'excluded': excluded or [], 'totals': {},
              'analytics': {'available': False}, 'issues': [], 'unrealized': None,
              'audit': {'passed': False}, 'generated_at': now}
    for account in accounts:
        snapshot = account.get('snapshot', {})
        result['accounts'].append({'exchange': account['exchange'], 'name': VENUES[account['exchange']],
            'captured_at': snapshot.get('captured_at'), 'ranges': merge(account.get('ranges', [])),
            'sync_status': account.get('sync', {}).get('status', 'unknown'), 'totals': {}})
    window = common_window(accounts, start, end)
    if not window:
        result['issues'].append('所选账户暂无共同的完整账单时段；请完成同步，或调整所选账户。' if accounts else '连接交易所并完成首次同步后，才会计算合并收益。')
        return result
    first, last = window
    result['window'] = {'start': first, 'end': last, 'shortened': first > start or last < end - 90000}
    totals, incomes, fills, ignored, pending, identities = {}, [], [], {}, [], {}
    with localcontext() as ctx:
        ctx.prec = 100
        for account, summary in zip(accounts, result['accounts']):
            venue, values = account['exchange'], {}
            for raw in account.get('income', []):
                try:
                    stamp = raw['time']
                    if isinstance(stamp, bool) or not isinstance(stamp, int):
                        raise ValueError('账单时间无效。')
                    if not first <= stamp <= last:
                        continue
                    kind, ident, asset = raw['incomeType'], raw['tranId'], raw['asset']
                    if type(ident) not in (str, int) or not isinstance(kind, str):
                        raise ValueError('账单编号或类型无效。')
                    ident = str(ident)
                    if not ident or ident == 'None' or not isinstance(asset, str) or not asset:
                        raise ValueError('账单编号或币种缺失。')
                    value = amount(raw['income'])
                    symbol = raw.get('symbol') or ''
                    if not isinstance(symbol, str):
                        raise ValueError('账单交易对字段无效。')
                    key = (venue, kind, ident)
                    signature = (stamp, asset, value, symbol)
                    if key in identities:
                        if identities[key] != signature:
                            raise ValueError('同一账单编号出现不同金额或属性，停止汇总。')
                        continue
                    identities[key] = signature
                    if kind in NON_TRADING or (symbol and not symbol.endswith('USDT')):
                        label = kind if kind in NON_TRADING else '其他产品'
                        item = ignored.setdefault((venue, label, asset), {'exchange': venue, 'type': label, 'asset': asset, 'count': 0, 'amount': Decimal(0)})
                        item['count'] += 1; item['amount'] += value
                        continue
                    mapped = KINDS.get(kind)
                    if not mapped:
                        if value or raw.get('unclassified'):
                            pending.append({'exchange': venue, 'type': kind, 'asset': asset, 'amount': str(value), 'id': ident})
                        continue
                    for target in (values, totals):
                        row = target.setdefault(asset, bucket())
                        row[mapped] += value; row['net'] += value
                    incomes.append(dict(raw, income=str(value), exchange=venue))
                except (KeyError, TypeError, ValueError) as exc:
                    result['issues'].append(VENUES[venue] + '：' + (str(exc) if isinstance(exc, ValueError) else '账单必要字段缺失。'))
            summary['totals'] = export(values)
            seen = {}
            for fill in account.get('trades', []):
                if not isinstance(fill, dict) or type(fill.get('time')) is not int or not isinstance(fill.get('symbol'), str) or type(fill.get('id')) not in (str, int) or not str(fill.get('id', '')).strip():
                    result['issues'].append(VENUES[venue] + '：成交字段无效，停止汇总。')
                    continue
                if first <= fill['time'] <= last and fill['symbol'].endswith('USDT'):
                    key = (fill['symbol'], str(fill['id']))
                    if key in seen:
                        if seen[key] != fill:
                            result['issues'].append(VENUES[venue] + '：同一成交编号有冲突。')
                        continue
                    seen[key] = fill
                    fills.append(dict(fill, exchange=venue))
        result['excluded'].extend(dict(item, amount=str(item['amount'])) for item in ignored.values())
        result['pending'] = pending
        if pending:
            result['issues'].append('存在尚未分类的账单，合并净收益暂不发布。')
        result['known_totals'] = export(totals)
        if result['issues']:
            return result
        # A complete empty USDT window is a verified zero, not missing evidence.
        totals.setdefault('USDT', bucket())
        for summary in result['accounts']:
            summary['totals'].setdefault('USDT', {key: '0' for key in bucket()})
        analytics = build_analytics(incomes, fills, {'complete': True, 'start': first, 'end': last})
        net = totals['USDT']['net']
        venue_sum = sum((Decimal(a['totals']['USDT']['net']) for a in result['accounts']), Decimal(0))
        day_sum = sum((Decimal(day['net']) for day in analytics['days']), Decimal(0))
        symbol_sum = sum((Decimal(row['net']) for row in analytics['symbols']), Decimal(0))
        components = totals['USDT']['realized'] + totals['USDT']['fees'] + totals['USDT']['funding']
        passed = net == venue_sum == day_sum == symbol_sum == components
        result['audit'] = {'passed': passed, 'ledger_count': len(incomes), 'account_sum': str(venue_sum),
            'day_sum': str(day_sum), 'symbol_sum': str(symbol_sum), 'component_sum': str(components)}
        if not passed:
            result['issues'].append('账户、日期、交易对与费用分项未对平，停止展示合计。')
            return result
        result.update(available=True, totals=export(totals), analytics=analytics)
        # Unrealized P/L remains independent from the realized ledger and its period.
        pnl = Decimal(0)
        fresh = True
        for account in accounts:
            snapshot, status = account.get('snapshot', {}), account.get('sync', {})
            if not snapshot.get('captured_at') or not -5000 <= now - snapshot['captured_at'] <= 90000 or not isinstance(snapshot.get('positions'), list) or account.get('enabled') is False or status.get('status') not in ('complete', 'running'):
                fresh = False; break
            try:
                for position in snapshot['positions']:
                    if position['symbol'].endswith('USDT'):
                        pnl += amount(position['unRealizedProfit'])
            except (ValueError, KeyError, TypeError, AttributeError):
                fresh = False; break
        if fresh:
            result['unrealized'] = str(pnl)
    return result


def report(db, days=30, exchanges=None, binance_configured=False, now=None):
    import journal
    from exchange_sync import public_config
    if days not in (7, 30, 90):
        raise ValueError('请选择近 7、30 或 90 天。')
    if exchanges is not None and (not exchanges or len(exchanges) != len(set(exchanges)) or any(x not in VENUES for x in exchanges)):
        raise ValueError('请选择 Binance、OKX 或 Bybit 账户，每个账户只选一次。')
    now = journal.now_ms() if now is None else now
    accounts, excluded = [], []
    for venue in VENUES:
        snapshot = journal.kv('snapshot' if venue == 'binance' else f'exchange:{venue}:snapshot', {}, db)
        cfg = {'configured': binance_configured, **journal.kv('recorder_settings', {}, db)} if venue == 'binance' else public_config(venue)
        if exchanges is not None:
            include = venue in exchanges
        else:
            include = bool(snapshot or cfg.get('configured'))
        if not include:
            excluded.append({'exchange': venue, 'reason': '未选择' if exchanges is not None else '尚未连接'})
            continue
        if venue == 'binance':
            income = [json.loads(r[0]) for r in db.execute('SELECT raw FROM income WHERE time BETWEEN ? AND ?', (now-days*DAY, now))]
            trades = [json.loads(r[0]) for r in db.execute('SELECT raw FROM trades WHERE time BETWEEN ? AND ?', (now-days*DAY, now))]
        else:
            income = [json.loads(r[0]) for r in db.execute('SELECT raw FROM exchange_income WHERE exchange=? AND time BETWEEN ? AND ?', (venue, now-days*DAY, now))]
            trades = [json.loads(r[0]) for r in db.execute('SELECT raw FROM exchange_trades WHERE exchange=? AND time BETWEEN ? AND ?', (venue, now-days*DAY, now))]
        verified_ranges = ranges(db, venue)
        status = journal.kv('recorder_status' if venue == 'binance' else f'exchange:{venue}:status', {}, db)
        accounts.append({'exchange': venue, 'snapshot': snapshot, 'enabled': cfg.get('enabled', True),
                         'sync': status, 'ranges': verified_ranges, 'income': income, 'trades': trades})
    excluded.append({'exchange': 'variational', 'reason': '仅手动 / 截图记录，未有可对账账单，不计入合计'})
    return build(accounts, now-days*DAY, now, now, excluded)
