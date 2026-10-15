"""Auditable, fixed-rule historical simulations for Binance USDⓈ-M perpetuals.

No private API, execution, trained model, parameter search, or inferred screenshot
indicator is involved. Decimal arithmetic is used for the complete trade ledger.
"""
import hashlib
import json
import math
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

import journal
from binance_client import Binance

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / 'research'
HOUR = 3600000
VERSION = '1.0.0'
PARAMS = dict(lookback=24, atr_period=14, reward_r=2, stop_atr=1.5, reclaim_buffer_atr=.25)
RISK = Decimal('100')
ZERO = Decimal(0)
DOC_URL = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
SOURCE_URLS = [DOC_URL, 'https://fapi.binance.com/fapi/v1/klines', 'https://fapi.binance.com/fapi/v1/fundingRate']
LIMITATIONS = [
    '透明固定规则研究基线，未训练预测模型，未识别或复现私人指标；需用户确认规则与原意一致。',
    '1h K线无法确定盘内路径；同根止损/目标双触发按止损先行，盘内退出时间记为该根收盘上界。',
    '退出K线内资金费先后未知时计入支付、忽略收入；开仓同一毫秒的结算不计，跳空退出同一毫秒计支付不计收入。',
    '固定每笔计划价格风险100 USDT，以实际入场价至止损价归一化；费用、跳空可使亏损超过1R，金额不代表账户收益。',
    '回撤为已平仓净损益曲线回撤，不是盘中权益回撤；未模拟盘口冲击、最小数量/步长、保证金或真实强平引擎。',
    '60/20/20按时间拆分，每段末收盘强平；指标允许使用边界前已知历史，信号和持仓不跨段。',
    '规则未调参，所有方向与实验均保留；研究留出段在报告后已被查看，修改规则需新的留出段或纸面前向验证。',
    '资金费覆盖available只表示接口成功返回该窗口记录和必要标记价，无法保证交易所历史接口没有漏报。',
]


class DataQualityError(ValueError):
    pass


def dec(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('数值格式无效。') from None
    if not result.is_finite():
        raise ValueError('数值必须有限。')
    return result


def number(value):
    if value is None:
        return None
    result = format(dec(value).quantize(Decimal('.000000000001')), 'f').rstrip('0').rstrip('.')
    return result if result and result != '-0' else '0'


def strategies():
    return [dict(id=sid, name=name, version=VERSION, kind='transparent_rule_baseline',
                 directions=['LONG', 'SHORT'], params=dict(PARAMS), rules=rules,
                 limitations=LIMITATIONS[:1], rules_confirmed=False) for sid, name, rules in [
        ('support_reclaim', '区间下沿收复 / 上沿跌回', {
            'LONG': '信号根最低价跌破此前24根最低价，收盘重回该下沿之上；下一根开盘买入。止损为信号最低价−0.25×ATR14，目标为实际入场风险的2R。',
            'SHORT': '信号根最高价突破此前24根最高价，收盘跌回该上沿之下；下一根开盘卖出。止损为信号最高价+0.25×ATR14，目标为实际入场风险的2R。'}),
        ('breakout', '区间前高 / 前低突破', {
            'LONG': '信号收盘高于此前24根最高价；下一根开盘买入。止损固定为信号收盘−1.5×ATR14，目标为实际入场风险的2R。',
            'SHORT': '信号收盘低于此前24根最低价；下一根开盘卖出。止损固定为信号收盘+1.5×ATR14，目标为实际入场风险的2R。'}),
    ]]


def normalize_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError('回测请求必须为对象。')
    symbol = str(payload.get('symbol', 'ETHUSDT')).upper().strip()
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT', symbol):
        raise ValueError('只支持 USDT 本位永续交易对，例如 ETHUSDT。')
    sid = payload.get('strategy_id', 'support_reclaim')
    direction = payload.get('direction', 'LONG')
    if sid not in {'support_reclaim', 'breakout'} or direction not in {'LONG', 'SHORT'}:
        raise ValueError('规则或方向无效。')
    if payload.get('interval', '1h') != '1h':
        raise ValueError('当前规则版本只支持1h K线。')
    days = payload.get('days', 90)
    if isinstance(days, bool) or str(days) not in {'30', '90'}:
        raise ValueError('回测窗口只支持30或90天。')
    dataset_id = payload.get('dataset_id', 'latest')
    if dataset_id not in {'latest', 'eth-1h-20261015'} or (dataset_id != 'latest' and symbol != 'ETHUSDT'):
        raise ValueError('未知历史数据集；只允许预先归档的ETH研究数据编号。')
    params = payload.get('params') or {}
    if not isinstance(params, dict) or set(params) - set(PARAMS):
        raise ValueError('规则参数无效。')
    if any(dec(value) != dec(PARAMS[key]) for key, value in params.items()):
        raise ValueError('当前为固定参数基线；调整参数需要新的规则版本和新的留出验证。')
    fee = dec(payload.get('fee_bps', 5))
    slip = dec(payload.get('slippage_bps', 2))
    if not ZERO <= fee <= 100 or not ZERO <= slip <= 100:
        raise ValueError('单边手续费和滑点需介于0至100基点。')
    return dict(symbol=symbol, interval='1h', days=int(days), strategy_id=sid,
                strategy_version=VERSION, direction=direction, params=dict(PARAMS),
                fee_bps=number(fee), slippage_bps=number(slip), dataset_id=dataset_id)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS backtest_runs (id TEXT PRIMARY KEY, created INTEGER NOT NULL,
      updated INTEGER NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS backtest_runs_created ON backtest_runs(created);
    ''')


def init():
    """Call once at service startup; interrupted runs remain visible as failures."""
    journal.DB.parent.mkdir(parents=True, exist_ok=True)
    with journal.connect() as db:
        _ensure_schema(db)
        interrupted = [json.loads(row['data']) for row in db.execute("SELECT data FROM backtest_runs WHERE status='running'")]
    for report in interrupted:
        report.update(status='failed', updated=journal.now_ms(), error='服务重启导致回测中断；本次实验已保留，可另开实验重跑。')
        _save(report)


def _save(report, new=False):
    directory = RESEARCH / 'experiments'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (report['id'] + '.json')
    report['archive_path'] = str(path)
    serialized = _json(report)
    with journal.connect() as db:
        _ensure_schema(db)
        if new:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT id FROM backtest_runs WHERE status='running' LIMIT 1").fetchone()
            if active:
                raise ValueError('已有回测进行中：' + active['id'] + '；请等待该实验完成。')
        db.execute('INSERT INTO backtest_runs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,status=excluded.status,data=excluded.data',
                   (report['id'], report['created'], report['updated'], report['status'], serialized))
    temp = path.with_suffix('.tmp')
    temp.write_text(serialized)
    temp.replace(path)


def _brief(report):
    return {key: value for key, value in report.items() if key not in {'trades', 'cost_sensitivity', 'equity_curve'}}


def list_runs():
    with journal.connect() as db:
        _ensure_schema(db)
        return [_brief(json.loads(row['data'])) for row in db.execute('SELECT data FROM backtest_runs ORDER BY created DESC,id DESC')]


def get_run(run_id):
    with journal.connect() as db:
        _ensure_schema(db)
        row = db.execute('SELECT data FROM backtest_runs WHERE id=?', (str(run_id),)).fetchone()
    return json.loads(row['data']) if row else None


def _new(payload):
    config = normalize_payload(payload)
    stamp = journal.now_ms()
    report = dict(config, id='bt-' + uuid.uuid4().hex, created=stamp, updated=stamp,
                  status='running', kind='historical_simulation', rules_confirmed=False,
                  data_source=None, data_hash=None, data=None, summary=None, splits=[],
                  trades=[], cost_sensitivity=[], limitations=list(LIMITATIONS))
    report['rules_hash'] = hashlib.sha256(_json(dict(strategy_id=config['strategy_id'],
        version=VERSION, direction=config['direction'], params=PARAMS)).encode()).hexdigest()
    _save(report, new=True)
    return report


def start(payload):
    report = _new(payload)
    # Return the original snapshot even if the worker finishes immediately.
    response = _brief(json.loads(_json(report)))
    threading.Thread(target=_execute, args=(report,), daemon=True, name=report['id']).start()
    return response


def run(payload):
    return _execute(_new(payload))


def _public_get(client, path, params=None):
    if path not in {'/fapi/v1/time', '/fapi/v1/exchangeInfo', '/fapi/v1/klines', '/fapi/v1/fundingRate'}:
        raise ValueError('回测只允许公开行情 GET。')
    url = 'https://fapi.binance.com' + path + '?' + urllib.parse.urlencode(params or {})
    req = urllib.request.Request(url, headers={'User-Agent': 'LocalTradeJournalBacktest/1.0'}, method='GET')
    try:
        # Reuses the app's verified TLS and normal environment proxy handling.
        with client.opener.open(req, timeout=25) as response:
            data = json.load(response)
        if isinstance(data, dict) and 'code' in data and data['code'] != 0:
            raise ValueError('Binance公开数据返回错误。')
        return data
    except urllib.error.HTTPError as exc:
        raise ValueError(f'Binance公开数据 HTTP {exc.code}；本次停止，不切换代理绕过限制。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError('Binance公开行情连接失败；未更改代理或TLS配置。') from None


def _archive_data(data, label):
    directory = RESEARCH / 'market-data'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (label + '-' + uuid.uuid4().hex[:12] + '.json')
    raw = _json(data).encode()
    path.write_bytes(raw)
    return data, str(path), hashlib.sha256(raw).hexdigest()


def load_data(config):
    existing = RESEARCH / 'ETHUSDT-1h-90d-input.json'
    archived = config.get('dataset_id') == 'eth-1h-20261015'
    if archived and not existing.exists():
        raise ValueError('指定的ETH研究归档不存在；不会悄悄改为其他时间窗口。')
    if config['symbol'] == 'ETHUSDT' and existing.exists():
        raw = existing.read_bytes()
        data = json.loads(raw)
        expected_end = journal.now_ms() // HOUR * HOUR - 1
        if archived or int(data.get('requested_end', 0)) == expected_end:
            if config['days'] == 90:
                return data, str(existing), hashlib.sha256(raw).hexdigest()
            validate_data(data)
            data['bars'] = data['bars'][-720:]
            data['requested_start'] = data['bars'][0][0]
            data['funding'] = [r for r in data['funding'] if data['requested_start'] <= int(r['fundingTime']) <= data['requested_end']]
            data['parent_data_hash'] = hashlib.sha256(raw).hexdigest()
            return _archive_data(data, 'ETHUSDT-1h-30d')
    client = Binance()  # No credentials loaded or needed.
    exchange = _public_get(client, '/fapi/v1/exchangeInfo')
    symbols = exchange.get('symbols', [])
    if not any(r.get('symbol') == config['symbol'] and r.get('contractType') == 'PERPETUAL'
               and r.get('quoteAsset') == 'USDT' and r.get('marginAsset') == 'USDT' for r in symbols):
        raise ValueError('交易所未确认该交易对为USDT本位永续，已停止。')
    now = int(_public_get(client, '/fapi/v1/time')['serverTime'])
    end = now // HOUR * HOUR - 1
    start_at = end + 1 - config['days'] * 24 * HOUR
    data = dict(source='Binance USDⓈ-M public REST', symbol=config['symbol'], interval='1h',
                requested_start=start_at, requested_end=end, retrieved_at=now,
                bars=[], funding=[], funding_error=None, source_urls=SOURCE_URLS)
    cursor = start_at
    try:
        while cursor <= end:
            batch = _public_get(client, '/fapi/v1/klines', dict(symbol=config['symbol'], interval='1h', startTime=cursor, endTime=end, limit=1000))
            if not isinstance(batch, list) or not batch:
                break
            data['bars'].extend(batch)
            following = int(batch[-1][0]) + HOUR
            if following <= cursor:
                raise DataQualityError('K线分页没有前进。')
            cursor = following
        cursor = start_at
        try:
            while cursor <= end:
                batch = _public_get(client, '/fapi/v1/fundingRate', dict(symbol=config['symbol'], startTime=cursor, endTime=end, limit=1000))
                if not isinstance(batch, list):
                    raise ValueError('资金费返回格式无效。')
                if not batch:
                    break
                data['funding'].extend(batch)
                following = int(batch[-1]['fundingTime']) + 1
                if following <= cursor:
                    raise DataQualityError('资金费分页没有前进。')
                cursor = following
                if len(batch) < 1000:
                    break
        except (ValueError, KeyError, TypeError) as exc:
            data['funding_error'] = str(exc)
    except Exception:
        _archive_data(data, config['symbol'] + '-partial')
        raise
    data['retrieved_at'] = max(now, journal.now_ms())
    return _archive_data(data, config['symbol'] + '-1h-' + str(config['days']) + 'd')


def validate_data(data):
    """Reject irregular candles rather than forward-fill or silently drop them."""
    bars = data.get('bars', [])
    if not isinstance(bars, list) or len(bars) < 30:
        raise DataQualityError('K线不足30根，不能形成规则与时间拆分。')
    retrieved = int(data.get('retrieved_at', 0))
    expected_start = int(data.get('requested_start', bars[0][0]))
    expected_end = int(data.get('requested_end', bars[-1][6]))
    gaps = []
    try:
        for i, bar in enumerate(bars):
            if len(bar) < 7:
                raise DataQualityError('K线字段不足。')
            opened, closed = int(bar[0]), int(bar[6])
            if opened % HOUR or closed != opened + HOUR - 1:
                raise DataQualityError('K线时间不符合完整1h周期。')
            if closed >= retrieved:
                raise DataQualityError('发现未收盘K线，已停止。')
            if i and opened != int(bars[i-1][0]) + HOUR:
                gaps.append(dict(previous=int(bars[i-1][0]), current=opened))
            o, h, l, c, volume = map(dec, bar[1:6])
            if min(o, h, l, c) <= 0 or volume < 0 or h < max(o, l, c) or l > min(o, h, c):
                raise DataQualityError('K线OHLC或成交量不一致。')
        if gaps:
            raise DataQualityError('K线存在时间缺口、重复或乱序；不补齐虚拟价格。')
        if int(bars[0][0]) != expected_start or int(bars[-1][6]) != expected_end:
            raise DataQualityError('K线起止范围不完整，存在边界缺口。')
        funding_seen = set()
        funding_complete = not data.get('funding_error') and bool(data.get('funding'))
        for row in data.get('funding', []):
            stamp = int(row['fundingTime'])
            if stamp in funding_seen or not expected_start <= stamp <= expected_end:
                raise DataQualityError('资金费重复或超出数据窗口。')
            if row.get('symbol', data.get('symbol')) != data.get('symbol'):
                raise DataQualityError('资金费交易对不匹配。')
            funding_seen.add(stamp)
            dec(row['fundingRate'])
            if not row.get('markPrice') or dec(row['markPrice']) <= 0:
                funding_complete = False
    except DataQualityError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise DataQualityError('行情数据字段格式错误。') from exc
    return dict(bar_count=len(bars), first_open=int(bars[0][0]), last_close=int(bars[-1][6]),
                retrieved_at=retrieved, funding_count=len(data.get('funding', [])),
                funding_coverage='available' if funding_complete else 'missing_or_incomplete', gaps=gaps,
                checks=dict(candles_unique=True, hour_spacing_complete=True, all_closed=True,
                            ohlc_consistent=True, volume_nonnegative=True, complete_range=True),
                source_urls=SOURCE_URLS)


def _signal(bars, index, config):
    if index < PARAMS['lookback']:
        return None
    previous = bars[index-24:index]
    low = min(b['low'] for b in previous)
    high = max(b['high'] for b in previous)
    bar = bars[index]
    # Simple (not Wilder-smoothed) ATR14, through the closed signal candle.
    trs = [max(bars[k]['high']-bars[k]['low'], abs(bars[k]['high']-bars[k-1]['close']),
               abs(bars[k]['low']-bars[k-1]['close'])) for k in range(index-13, index+1)]
    atr = sum(trs, ZERO) / 14
    if atr <= 0:
        return None
    long = config['direction'] == 'LONG'
    if config['strategy_id'] == 'support_reclaim':
        trigger = bar['low'] < low < bar['close'] if long else bar['high'] > high > bar['close']
        stop = bar['low'] - atr/4 if long else bar['high'] + atr/4
        level = low if long else high
    else:
        trigger = bar['close'] > high if long else bar['close'] < low
        stop = bar['close'] - atr*dec('1.5') if long else bar['close'] + atr*dec('1.5')
        level = high if long else low
    if not trigger or stop <= 0:
        return None
    return dict(index=index, stop=stop, level=level, atr=atr)


def _funding(data, position, exit_time, exit_bar_open, intrabar):
    total = ZERO
    events = []
    for row in data.get('funding', []):
        stamp = int(row['fundingTime'])
        if not position['entry_time'] < stamp <= exit_time:
            continue
        if not row.get('markPrice') or dec(row['markPrice']) <= 0:
            events.append(dict(time=stamp, rate=row['fundingRate'], mark_price=None,
                               cost_usdt=None, applied=False, reason='missing_mark_price'))
            continue
        cost = position['quantity'] * dec(row['markPrice']) * dec(row['fundingRate']) * position['sign']
        uncertain = (intrabar and stamp >= exit_bar_open) or stamp == exit_time
        applied = not uncertain or cost >= 0
        if applied:
            total += cost
        events.append(dict(time=stamp, rate=number(row['fundingRate']), mark_price=number(row['markPrice']),
                           cost_usdt=number(cost), applied=applied,
                           reason='exit_order_unknown_payment_included' if uncertain and applied else
                                  'exit_order_unknown_credit_omitted' if uncertain else 'held_at_settlement'))
    return total, events


def _close_trade(data, bars, position, index, reference, reason, config, split, ambiguous):
    sign = position['sign']
    slip = dec(config['slippage_bps']) / 10000
    fee = dec(config['fee_bps']) / 10000
    exit_price = reference * (1 - sign*slip)
    at_open = reason in {'stop_gap', 'target_gap'}
    exit_time = bars[index]['time'] if at_open else bars[index]['close_time']
    funding, events = _funding(data, position, exit_time, bars[index]['time'], not at_open and reason != 'boundary')
    quantity = position['quantity']
    gross = (exit_price-position['entry']) * quantity * sign
    entry_fee = position['entry'] * quantity * fee
    exit_fee = exit_price * quantity * fee
    fees = entry_fee + exit_fee
    net = gross - fees - funding
    slip_cost = (abs(position['entry']-position['reference']) + abs(exit_price-reference)) * quantity
    result = dict(id=f'{split}-{position["entry_index"]}', split=split, direction=config['direction'],
                  signal_time=bars[position['signal']['index']]['close_time'], entry_time=position['entry_time'],
                  exit_time=exit_time, signal_index=position['signal']['index'], entry_index=position['entry_index'],
                  exit_index=index, exit_reason=reason, funding_events=events, ambiguous_intrabar=ambiguous)
    result.update({key: number(value) for key, value in dict(
        signal_level=position['signal']['level'], atr=position['signal']['atr'], entry_reference=position['reference'],
        entry_price=position['entry'], stop_loss=position['stop'], take_profit=position['target'],
        exit_reference=reference, exit_price=exit_price, quantity=quantity, risk_usdt=RISK,
        gross_pnl_usdt=gross, entry_fee_usdt=entry_fee, exit_fee_usdt=exit_fee, fees_usdt=fees,
        slippage_usdt=slip_cost, funding_cost_usdt=funding, net_pnl_usdt=net, gross_r=gross/RISK, net_r=net/RISK).items()})
    return result


def simulate(data, config):
    bars = [dict(time=int(b[0]), open=dec(b[1]), high=dec(b[2]), low=dec(b[3]), close=dec(b[4]),
                 close_time=int(b[6])) for b in data['bars']]
    n = len(bars)
    boundaries = [0, n*60//100, n*80//100, n]
    trades, splits = [], []
    skipped = 0
    for segment, name, label in [(0,'development','开发段 60%'),(1,'validation','验证段 20%'),(2,'holdout','研究留出段 20%')]:
        start_at, end_at = boundaries[segment:segment+2]
        position = None
        segment_trades = []
        for index in range(start_at, end_at):
            bar = bars[index]
            if position is None and index > start_at:
                signal = _signal(bars, index-1, config)
                if signal:
                    sign = Decimal(1 if config['direction'] == 'LONG' else -1)
                    entry = bar['open'] * (1 + sign*dec(config['slippage_bps'])/10000)
                    distance = (entry-signal['stop']) * sign
                    # A stop already invalidated at the next open cannot become a new valid setup.
                    if (bar['open']-signal['stop'])*sign <= 0 or distance <= 0:
                        skipped += 1
                    else:
                        target = entry + sign*distance*2
                        if target <= 0:
                            skipped += 1
                            continue
                        position = dict(signal=signal, sign=sign, entry=entry, reference=bar['open'],
                                        stop=signal['stop'], target=target, quantity=RISK/distance,
                                        entry_time=bar['time'], entry_index=index)
            if position is None:
                continue
            long = position['sign'] > 0
            stop, target = position['stop'], position['target']
            reason, reference, ambiguous = None, None, False
            stop_gap = bar['open'] <= stop if long else bar['open'] >= stop
            target_gap = bar['open'] >= target if long else bar['open'] <= target
            stop_hit = bar['low'] <= stop if long else bar['high'] >= stop
            target_hit = bar['high'] >= target if long else bar['low'] <= target
            if stop_gap:
                reason, reference = 'stop_gap', bar['open']
            elif target_gap:
                # Do not award favorable gap price improvement to a target order.
                reason, reference = 'target_gap', target
            elif stop_hit:
                reason, reference, ambiguous = 'stop', stop, bool(target_hit)
            elif target_hit:
                reason, reference = 'target', target
            elif index == end_at-1:
                reason, reference = 'boundary', bar['close']
            if reason:
                segment_trades.append(_close_trade(data, bars, position, index, reference, reason, config, name, ambiguous))
                position = None
        trades.extend(segment_trades)
        splits.append(dict(name=name, label=label, start_index=start_at, end_index=end_at,
                           first_open=bars[start_at]['time'], last_close=bars[end_at-1]['close_time'],
                           summary=metrics(segment_trades)))
    return dict(trades=trades, splits=splits, summary=metrics(trades), skipped_invalid_open=skipped)


def metrics(trades):
    count = len(trades)
    net = [dec(t['net_r']) for t in trades]
    wins = sum(v > 0 for v in net)
    losses = sum(v < 0 for v in net)
    profits = sum((v for v in net if v > 0), ZERO)
    loss = -sum((v for v in net if v < 0), ZERO)
    pf = number(profits/loss) if loss else ('infinity' if profits else None)
    payoff = number((profits/wins)/(loss/losses)) if wins and losses else ('infinity' if wins else None)
    lo = hi = None
    if count:
        z = 1.959963984540054
        p = wins/count
        middle = (p + z*z/(2*count))/(1+z*z/count)
        spread = z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/(1+z*z/count)
        lo, hi = number(max(0,middle-spread)), number(min(1,middle+spread))
    equity, peak, drawdown = ZERO, ZERO, ZERO
    for value in net:
        equity += value
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
    result = dict(trade_count=count,wins=wins,losses=losses,breakeven=count-wins-losses,
                  win_rate=number(dec(wins)/count) if count else None,
                  win_rate_ci=dict(lower=lo,upper=hi,confidence='0.95'),
                  net_expectancy_r=number(sum(net,ZERO)/count) if count else None,
                  gross_expectancy_r=number(sum((dec(t['gross_r']) for t in trades),ZERO)/count) if count else None,
                  profit_factor=pf,payoff_ratio=payoff,max_drawdown_r=number(drawdown),
                  max_drawdown_usdt=number(drawdown*RISK),evidence_status='insufficient',evidence_reasons=[])
    for key in ('net_pnl_usdt','gross_pnl_usdt','fees_usdt','slippage_usdt','funding_cost_usdt'):
        result[key] = number(sum((dec(t[key]) for t in trades),ZERO))
    result['evidence_reasons'] = ['该段少于10笔，样本不足。'] if count < 10 else []
    if count >= 10:
        result['evidence_status'] = 'provisional_support' if sum(net,ZERO) > 0 else 'not_supported'
        if sum(net,ZERO) <= 0:
            result['evidence_reasons'].append('该段含成本净期望不为正。')
    return result


def _assess(report):
    insufficient, unsupported = [], []
    summary = report['summary']
    if report['data']['bar_count'] < 2160:
        insufficient.append('不足90天K线，仅可探索，历史证据不足。')
    if summary['trade_count'] < 30:
        insufficient.append('总交易少于30笔，胜率与净期望估计不稳定。')
    for split in report['splits'][1:]:
        if split['summary']['trade_count'] < 10:
            insufficient.append(split['label'] + '少于10笔。')
    if report['data']['funding_coverage'] != 'available':
        insufficient.append('资金费缺失或不完整，报告未覆盖全部成本。')
    if summary['net_expectancy_r'] is not None and dec(summary['net_expectancy_r']) <= 0:
        unsupported.append('整体含成本净期望不为正，不支持该规则。')
    for split in report['splits'][1:]:
        expectation = split['summary']['net_expectancy_r']
        if expectation is not None and dec(expectation) <= 0:
            unsupported.append(split['label'] + '净期望不为正，未呈现稳定优势。')
    stressed = report['cost_sensitivity'][1]['summary']['net_expectancy_r']
    if stressed is not None and dec(stressed) <= 0:
        unsupported.append('双倍执行成本下净期望不为正，成本敏感。')
    summary['evidence_status'] = 'insufficient' if insufficient else 'not_supported' if unsupported else 'provisional_support'
    summary['evidence_reasons'] = insufficient + unsupported
    if not summary['evidence_reasons']:
        summary['evidence_reasons'] = ['有限历史研究证据，需规则确认和新的前向样本；不保证未来收益。']


def _execute(report):
    try:
        data, path, digest = load_data(report)
        report.update(data_source=data.get('source','未知来源'),data_hash=digest,
                      data=dict(path=path,retrieved_at=data.get('retrieved_at'),source_urls=SOURCE_URLS))
        if data.get('symbol') != report['symbol'] or data.get('interval') != report['interval']:
            raise DataQualityError('原始数据交易对或周期与实验不一致。')
        report['data'].update(validate_data(data))
        result = simulate(data,report)
        report.update(result)
        scenarios = [('基准成本',dec(report['fee_bps']),dec(report['slippage_bps'])),
                     ('双倍执行成本',dec(report['fee_bps'])*2,dec(report['slippage_bps'])*2),
                     ('零手续费与滑点（资金费保留）',ZERO,ZERO)]
        for i, (label,fee,slip) in enumerate(scenarios):
            scenario = result if i == 0 else simulate(data,dict(report,fee_bps=number(fee),slippage_bps=number(slip)))
            report['cost_sensitivity'].append(dict(label=label,fee_bps=number(fee),slippage_bps=number(slip),summary=dict(scenario['summary'])))
        _assess(report)
        report.update(status='completed',updated=journal.now_ms())
    except Exception as exc:
        report.update(status='failed',updated=journal.now_ms(),error=str(exc)[:800],summary=None,
                      splits=[],trades=[],cost_sensitivity=[])
    _save(report)
    return report
