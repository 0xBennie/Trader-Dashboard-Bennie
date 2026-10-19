"""Binance USDⓈ-M official public flow data. Only samples at/before the decision; failures stay explicit."""
import market_replay

PATHS = {'premium': 'https://fapi.binance.com/fapi/v1/premiumIndex',
         'taker': 'https://fapi.binance.com/futures/data/takerlongshortRatio',
         'top': 'https://fapi.binance.com/futures/data/topLongShortPositionRatio',
         'oi': 'https://fapi.binance.com/futures/data/openInterestHist'}
PERIODS = {'5m': 300000, '15m': 900000, '30m': 1800000, '1h': 3600000, '2h': 7200000,
           '4h': 14400000, '6h': 21600000, '12h': 43200000, '1d': 86400000}
SAMPLES = 12


def nearest_period(period):
    if period in PERIODS:
        return period
    try:
        ms = int(period[:-1])*{'m': 60000, 'h': 3600000, 'd': 86400000}[period[-1]]
    except (ValueError, KeyError, IndexError):
        return '5m'
    return min(PERIODS, key=lambda p: abs(PERIODS[p]-ms))


def _series(raw, symbol, anchor, field):
    if not isinstance(raw, list):
        raise ValueError('not a list')
    rows = sorted((int(r['timestamp']), float(r[field])) for r in raw
                  if int(r['timestamp']) <= anchor and r.get('symbol', symbol) == symbol)
    if not rows:
        raise ValueError('no samples')
    return rows[-SAMPLES:]


def binance_flow(symbol, period, anchor, getter=market_replay.public_get):
    period = nearest_period(period)
    out = dict(status='unavailable', period=period, symbol=symbol, mark_price=None, funding_rate=None,
               taker_ratio_last=None, taker_ratio_avg=None, top_long_short=None, oi_change_pct=None, through=None,
               note='Binance 官方公开数据，仅作参考；主动买卖比与头部账户多空比不等于本账户方向或未来走势。')
    params = dict(symbol=symbol, period=period, endTime=anchor, limit=SAMPLES+2)
    stamps = []; ok = 0
    try:
        p = getter(PATHS['premium'], dict(symbol=symbol))
        if p.get('symbol') == symbol:
            out.update(mark_price=float(p['markPrice']), funding_rate=float(p['lastFundingRate'])); ok += 1
    except Exception:
        pass
    for key, field in (('taker', 'buySellRatio'), ('top', 'longShortRatio'), ('oi', 'sumOpenInterest')):
        try:
            rows = _series(getter(PATHS[key], params), symbol, anchor, field)
        except Exception:
            continue
        ok += 1; stamps.append(rows[-1][0])
        if key == 'taker':
            out.update(taker_ratio_last=rows[-1][1], taker_ratio_avg=sum(v for _, v in rows)/len(rows))
        elif key == 'top':
            out['top_long_short'] = rows[-1][1]
        elif len(rows) >= 2 and rows[0][1] > 0:
            out['oi_change_pct'] = (rows[-1][1]/rows[0][1]-1)*100
    out['through'] = max(stamps) if stamps else None
    out['status'] = 'complete' if ok == 4 else 'partial' if ok else 'unavailable'
    return out
