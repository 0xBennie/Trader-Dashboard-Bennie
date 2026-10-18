"""Public perpetual candles tied to an explicit observation/entry time. No account calls."""
import hashlib
import json
import re
import ssl
import threading
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

import journal
import review_learning

INTERVALS = {'5m': 300000, '15m': 900000, '30m': 1800000, '1h': 3600000, '4h': 14400000, '1d': 86400000}
LOCK = threading.Lock()


def init(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS market_replays (
        id TEXT PRIMARY KEY, journal_id TEXT NOT NULL, created INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS replay_journal ON market_replays(journal_id,created DESC);''')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('行情接口重定向，未读取。')


def public_get(url, params):
    # Fixed hosts and paths only: user notes and provider configuration cannot select a URL.
    allowed = {'https://fapi.binance.com/fapi/v1/klines',
               'https://fapi.binance.com/futures/data/openInterestHist',
               'https://fapi.binance.com/fapi/v1/fundingRate',
               'https://api.bybit.com/v5/market/kline',
               'https://www.okx.com/api/v5/market/history-candles'}
    if url not in allowed:
        raise ValueError('行情地址不支持。')
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(url+'?'+urllib.parse.urlencode(params), headers={'User-Agent':'TraderJournal/1.0'}, method='GET')
    with opener.open(req, timeout=12) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('行情响应过大。')
    return json.loads(raw)


def instrument(exchange, symbol):
    if exchange not in {'variational', 'binance', 'okx', 'bybit'}:
        raise ValueError('交易所不支持。')
    symbol = str(symbol).upper().strip()
    if not re.fullmatch(r'[A-Z0-9]{1,24}', symbol):
        raise ValueError('合约名称无效。')
    base = symbol[:-4] if symbol.endswith('USDT') else symbol
    if not base:
        raise ValueError('合约名称无效。')
    source = 'binance' if exchange == 'variational' else exchange
    return dict(execution_exchange=exchange, exchange=source, symbol=base+'USDT',
                instrument=base+'-USDT-SWAP' if source == 'okx' else base+'USDT',
                product='USDT perpetual', price_type='last_trade', quote_currency='USDT',
                reference_only=exchange == 'variational',
                note='Binance USDT 永续参考行情；不是 Variational 标记价、报价或成交证明。' if exchange == 'variational'
                     else '同交易所 USDT 永续成交价 K 线；不等同标记价或止损成交价。')


def bars(source, interval, start, end, getter=public_get):
    step = INTERVALS[interval]
    venue, symbol = source['exchange'], source['instrument']
    if venue == 'binance':
        raw = getter('https://fapi.binance.com/fapi/v1/klines', dict(symbol=symbol, interval=interval, startTime=start, endTime=end, limit=499))
    elif venue == 'bybit':
        code = {'5m':'5','15m':'15','30m':'30','1h':'60','4h':'240','1d':'D'}[interval]
        answer = getter('https://api.bybit.com/v5/market/kline', dict(category='linear', symbol=symbol, interval=code, start=start, end=end, limit=499))
        if answer.get('retCode') != 0 or answer.get('result',{}).get('symbol') != symbol:
            raise ValueError('Bybit 合约行情未返回。')
        raw = answer['result']['list']
    else:
        code = {'1h':'1H','4h':'4H','1d':'1Dutc'}.get(interval, interval)
        answer = getter('https://www.okx.com/api/v5/market/history-candles', dict(instId=symbol, bar=code, after=end+1, before=start-1, limit=300))
        if answer.get('code') != '0':
            raise ValueError('OKX 合约行情未返回。')
        raw = answer['data']
    if not isinstance(raw, list):
        raise ValueError('K 线格式不正确。')
    result = {}
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError('K 线缺少字段。')
        opened = int(row[0]); closed = opened+step-1
        if opened < start or closed > end:
            continue  # Incomplete candles never enter the evidence packet.
        if venue == 'okx' and (len(row) < 9 or str(row[8]) != '1'):
            continue
        values = [Decimal(str(x)) for x in row[1:6]]
        o,h,l,c,v = values
        if not all(x.is_finite() for x in values) or min(o,h,l,c) <= 0 or v < 0 or h < max(o,c,l) or l > min(o,c,h):
            raise ValueError('K 线数值不正确。')
        candle = dict(time=opened, close_time=closed, open=str(o), high=str(h), low=str(l), close=str(c), volume=str(v))
        if opened in result and result[opened] != candle:
            raise ValueError('同一根 K 线出现冲突。')
        result[opened] = candle
    return sorted(result.values(), key=lambda row: row['time'])


def structure(candles):
    if len(candles) < 20:
        return {'available':False, 'reason':'完整 K 线不足 20 根。'}
    last = candles[-20:]
    tr = [max(Decimal(c['high'])-Decimal(c['low']), abs(Decimal(c['high'])-Decimal(p['close'])),
              abs(Decimal(c['low'])-Decimal(p['close']))) for p,c in zip(candles[-15:-1],candles[-14:])]
    close = Decimal(candles[-1]['close'])
    return dict(available=True, last_close=str(close), through=candles[-1]['close_time'],
                range_low=str(min(Decimal(c['low']) for c in last)), range_high=str(max(Decimal(c['high']) for c in last)),
                mean_close_20=str(sum(Decimal(c['close']) for c in last)/20),
                atr_14_sma=str(sum(tr)/len(tr)),
                change_20_pct=str((close/Decimal(last[0]['open'])-1)*100),
                method='最近 20 根已收盘 K 线高低区间；14 根 TR 简单均值，不是自动认定的支撑阻力。')


def conditions(source, window, anchor, getter):
    """Only samples timestamped at/before the decision; failures remain explicit."""
    result={'source_exchange':source['exchange'],'as_of':anchor,
            'open_interest':{'status':'unavailable'},'funding':{'status':'unavailable'},
            'relative_strength':{'status':'unavailable'},
            'note':'跨交易所时仅为参考；OI 增加不代表净多头增加，资金费不是本账户已付费用。新闻日历未核对。'}
    before=[c for c in window.get('candles',[]) if c['close_time']<=anchor][-20:]
    if source['symbol']!='BTCUSDT' and len(before)==20:
        try:
            benchmark=instrument(source['exchange'],'BTCUSDT')
            base=bars(benchmark,window['interval'],before[0]['time'],before[-1]['close_time'],getter)
            if [c['time'] for c in base]!=[c['time'] for c in before]:
                raise ValueError('对照行情时间未对齐')
            asset=(Decimal(before[-1]['close'])/Decimal(before[0]['open'])-1)*100
            btc=(Decimal(base[-1]['close'])/Decimal(base[0]['open'])-1)*100
            result['relative_strength']=dict(status='complete',asset_return_pct=str(asset),btc_return_pct=str(btc),
                                            excess_percentage_points=str(asset-btc),start=before[0]['time'],end=before[-1]['close_time'],
                                            note='相同时间窗口的涨跌幅差；不是预测信号或胜率。')
        except Exception:
            pass
    if source['exchange']=='binance':
        try:
            raw=getter('https://fapi.binance.com/futures/data/openInterestHist',dict(symbol=source['symbol'],period='5m',startTime=anchor-6*3600000,endTime=anchor,limit=100))
            rows=[]
            for r in raw:
                t=int(r['timestamp']);oi=Decimal(str(r['sumOpenInterest']))
                if r['symbol']==source['symbol'] and anchor-6*3600000<=t<=anchor and oi.is_finite() and oi>0:
                    rows.append(dict(time=t,contracts=str(oi)))
            rows=sorted({r['time']:r for r in rows}.values(),key=lambda r:r['time'])
            if len(rows)>=2:
                result['open_interest']=dict(status='complete',samples=rows,through=rows[-1]['time'],
                    change_pct=str((Decimal(rows[-1]['contracts'])/Decimal(rows[0]['contracts'])-1)*100),
                    freshness='recent' if anchor-rows[-1]['time']<=600000 else 'stale',
                    note='公开历史仅在交易所可查范围内；不是用户账户持仓，也不提供净多空方向。')
        except Exception:
            pass
        try:
            raw=getter('https://fapi.binance.com/fapi/v1/fundingRate',dict(symbol=source['symbol'],startTime=anchor-3*86400000,endTime=anchor,limit=100))
            rows=[]
            for r in raw:
                t=int(r['fundingTime']);rate=Decimal(str(r['fundingRate']))
                if r['symbol']==source['symbol'] and anchor-3*86400000<=t<=anchor and rate.is_finite():
                    rows.append(dict(time=t,rate=str(rate)))
            if rows:result['funding']=dict(status='complete',settled_rates=sorted(rows,key=lambda r:r['time'])[-8:],note='当时已结算的资金费率，不预测下一期，也不等于用户资金费支出。')
        except Exception:
            pass
    return result


def collect(record, now=None, getter=public_get, preflight=False):
    now = journal.now_ms() if now is None else now
    source = instrument(record.get('exchange','binance'), record['symbol'])
    opened = review_learning.opened(record)
    anchor = now if preflight else opened or (record.get('screenshot_snapshot') or {}).get('received_at') or record.get('created')
    if not isinstance(anchor, int) or isinstance(anchor, bool) or not 0 < anchor <= now:
        raise ValueError('缺少有效的入场时间或归档观察时间。')
    exact = bool(opened and not preflight)
    interval = record.get('timeframe') or '5m'
    unsupported = interval not in INTERVALS
    if unsupported:
        interval = '5m'
    intervals = list(dict.fromkeys([interval,'1h','4h']))
    closed = review_learning.closed(record)
    windows = []
    for frame in intervals:
        step = INTERVALS[frame]
        targets = [('decision' if preflight else 'entry' if exact else 'observation', anchor)]
        if not preflight and closed and closed > anchor+96*step:
            targets.append(('exit',closed))
        for kind, center in targets:
            start = max(0, center//step*step-120*step)
            end = min(now, center if preflight else center+96*step)
            w = dict(kind=kind, interval=frame, anchor=center, requested_start=start, requested_end=end)
            try:
                candles = bars(source,frame,start,end,getter)
                expected = max(0,(end+1)//step-start//step)
                before = [c for c in candles if c['close_time'] <= center]
                w.update(candles=candles, before_count=len(before), after_count=len(candles)-len(before),
                         structure_before=structure(before), coverage='complete' if len(candles)==expected and expected else 'partial',
                         first=candles[0]['time'] if candles else None, last=candles[-1]['close_time'] if candles else None,
                         status='complete' if candles else 'unavailable')
            except Exception:
                w.update(status='unavailable',candles=[],coverage='missing',error='此时段公开行情未取得；可能未上市、接口受限或网络不可用。')
            windows.append(w)
    return dict(source=source, collected_at=now, anchor=anchor,
                anchor_kind='preflight' if preflight else 'entry' if exact else 'recorded_observation',
                entry_time=opened if exact else None, exit_time=closed, decision_timeframe=(record.get('entry_interview') or {}).get('decision_timeframe'),
                requested_timeframe=record.get('timeframe'), interval_substitution=unsupported,
                status='complete' if all(w['status']=='complete' and w['coverage']=='complete' for w in windows)
                       else 'partial' if any(w['candles'] for w in windows) else 'unavailable',
                windows=windows, conditions_before=conditions(source,windows[0],anchor,getter), limitations=[
                    '按入场 / 归档及离场附近截取窗口，窗口之间未覆盖的走势不用于整笔最大浮盈浮亏计算。',
                    '事前分析只允许使用 anchor 之前已收盘的 K 线；入场后的 K 线仅用于事后复盘。',
                    *([] if exact or preflight else ['实际入场时间未知：本图围绕归档观察时间，不能据此判断入场精度。']),
                    *(['原图周期暂不支持，参考图改用 5m 并明确标记；未改写用户决策周期。'] if unsupported else []),
                    '历史 OI、资金费只在接口成功返回时使用，并标注交易所；新闻和私人指标公式未验证。'])


def latest(jid):
    with journal.connect() as db:
        row = db.execute('SELECT data FROM market_replays WHERE journal_id=? ORDER BY created DESC,rowid DESC LIMIT 1',(jid,)).fetchone()
    return json.loads(row[0]) if row else None


def capture(jid, force=False, getter=public_get):
    with journal.connect() as db:
        record = next((j for j in journal.all_journals(db) if j['id']==jid),None)
    if not record:
        raise ValueError('找不到这条交易记录。')
    previous = latest(jid)
    if previous and not force and previous.get('journal_updated')==record['updated'] and journal.now_ms()-previous['collected_at'] < 300000:
        return previous
    result = collect(record,getter=getter)
    result.update(journal_id=jid,journal_updated=record['updated'],
                  plan_levels={k:record.get(k) for k in ('entry_price','stop_loss','take_profit')})
    encoded = json.dumps(result,ensure_ascii=False)
    identity = hashlib.sha256(encoded.encode()).hexdigest()
    with journal.connect() as db:
        db.execute('INSERT OR IGNORE INTO market_replays VALUES(?,?,?,?)',(identity,jid,result['collected_at'],encoded))
    return result


def warm_one(now=None):
    """One bounded public-data capture per worker turn; not browser monitoring."""
    if not LOCK.acquire(blocking=False):
        return
    try:
        now = journal.now_ms() if now is None else now
        with journal.connect() as db:
            candidates = [j for j in journal.all_journals(db) if now-j['created'] <= 30*86400000 and j.get('status')!='计划中']
        for record in candidates:
            old = latest(record['id'])
            if old and (old['status']=='complete' and old.get('journal_updated')==record['updated'] or now-old['collected_at']<3600000):
                continue
            try:
                capture(record['id'])
            except ValueError:
                continue  # A record with no valid time must not starve the rest of the queue.
            return
    finally:
        LOCK.release()


def for_ai(replay):
    if not replay:
        return {'status':'not_captured','note':'本次尚未取得 K 线，不能声称已看走势。'}
    # Complete input windows stay in private storage; send compact, explicitly split evidence.
    return {**{k:v for k,v in replay.items() if k!='windows'},'windows':[
        {**{k:v for k,v in w.items() if k!='candles'},
         'before_anchor':[c for c in w['candles'] if c['close_time']<=w['anchor']][-60:],
         'after_anchor':[c for c in w['candles'] if c['close_time']>w['anchor']][:60]}
        for w in replay['windows']]}
