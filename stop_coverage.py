"""Read-only fixed-stop quantity coverage; never treats a journal plan as an order."""
from decimal import Decimal, InvalidOperation
import json


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def fresh(stamp, now):
    stamp = number(stamp)
    return stamp is not None and 0 <= now - stamp <= 90000


def summarize(state, now):
    snapshot = state.get('snapshot') or {}
    contexts = state.get('trade_context') or {}
    positions = snapshot.get('positions')
    snapshot_fresh = isinstance(positions, list) and fresh(snapshot.get('captured_at'), now)
    rows = []
    for p in positions or []:
        amount = number(p.get('positionAmt'))
        if amount == 0:
            continue
        symbol, side = p.get('symbol'), p.get('positionSide')
        qty = abs(amount) if amount is not None else None
        row = dict(symbol=symbol, position_side=side, direction='LONG' if amount is not None and amount > 0 else 'SHORT',
                   quantity=str(qty) if qty is not None else None, covered_quantity=None, coverage_pct=None,
                   status='unknown', reason='等待最新仓位和挂单', captured_at=None, stops=[], excluded=0)
        rows.append(row)
        context = contexts.get(symbol) or {}
        row['captured_at'] = context.get('captured_at')
        if not snapshot_fresh:
            row['reason'] = '仓位快照已过期'
            continue
        if amount is None or side not in {'BOTH', 'LONG', 'SHORT'} or (side == 'LONG' and amount < 0) or (side == 'SHORT' and amount > 0):
            row['reason'] = '仓位方向或数量待核对'
            continue
        if context.get('status') != 'complete' or not fresh(context.get('captured_at'), now) or not isinstance(context.get('orders'), list):
            row['reason'] = '挂单未更新或读取失败'
            continue
        matching = [old for old in context.get('positions', []) if old.get('symbol') == symbol and old.get('positionSide') == side]
        if len(matching) != 1 or any(number(matching[0].get(k)) != number(p.get(k)) for k in ('positionAmt', 'entryPrice', 'updateTime')):
            row['reason'] = '仓位已变化，等待重新核对挂单'
            continue
        covered, seen = Decimal(0), set()
        malformed = False
        for order in context['orders']:
            if order.get('symbol') != symbol or order.get('positionSide') != side or order.get('side') != ('SELL' if amount > 0 else 'BUY'):
                continue
            if order.get('coverage_unknown'):
                malformed = True
                continue
            if order.get('type') not in {'STOP', 'STOP_MARKET'} or order.get('status') not in {'NEW', 'PARTIALLY_FILLED'}:
                continue
            # Hedge-mode SELL LONG / BUY SHORT closes its own leg. One-way quantity stops must be reduce-only.
            close_all = order.get('closePosition') is True and order.get('type') == 'STOP_MARKET'
            if not (close_all or order.get('reduceOnly') is True or side in {'LONG', 'SHORT'}):
                row['excluded'] += 1
                continue
            trigger, remaining = number(order.get('triggerPrice')), number(order.get('quantity'))
            if trigger is None or trigger <= 0 or (not close_all and (remaining is None or remaining < 0)):
                malformed = True
                continue
            key = order.get('order_ref') or json.dumps(order, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            size = qty if close_all else remaining
            if not size:
                continue
            covered += size
            row['stops'].append(dict(price=str(trigger), quantity=str(size), type=order['type'],
                                     working_type=order.get('workingType'), price_protect=order.get('priceProtect') is True))
        if malformed:
            row['reason'] = '止损挂单字段不完整，需核对'
            continue
        covered = min(qty, covered)
        row.update(covered_quantity=str(covered), coverage_pct=str(covered / qty * 100),
                   status='full' if covered >= qty else 'partial' if covered else 'missing',
                   reason='已核对固定止损挂单数量')
    counts = {key: sum(r['status'] == key for r in rows) for key in ('full', 'partial', 'missing', 'unknown')}
    return dict(snapshot_fresh=snapshot_fresh, captured_at=snapshot.get('captured_at'), rows=rows, counts=counts)
