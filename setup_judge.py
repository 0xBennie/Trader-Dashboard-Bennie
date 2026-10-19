"""Pre-entry judgment: location → trigger → risk, scored from closed candles and the trader's own rules.
Deterministic; AI may explain the result but never changes it. Not a trading permit."""
import math

LABELS = {'go': '可以做', 'conditional': '有条件做', 'wait_trigger': '等触发', 'no': '别做'}
BULL = {'hammer', 'bull_engulfing', 'bull_pin'}
BEAR = {'shooting_star', 'bear_engulfing', 'bear_pin'}
COMBINED_MULTIPLE = 3  # same-direction open risk may reach 3 × the per-trade account percentage


def fmt(x):
    if x is None:
        return '—'
    if x == 0:
        return '0'
    dec = min(10, max(2, 6-math.floor(math.log10(abs(x)))))
    text = f'{x:.{dec}f}'.rstrip('0').rstrip('.')
    return text


def r2(x):
    return f'{x:.2f}'.rstrip('0').rstrip('.')


PATTERN_NAMES = {'hammer': '锤子线', 'shooting_star': '流星线', 'bull_engulfing': '看涨吞没', 'bear_engulfing': '看跌吞没',
                 'bull_pin': '看涨 Pin Bar', 'bear_pin': '看跌 Pin Bar', 'doji': '十字星', 'inside': '内包线'}


def floor_qty(q):
    if not q or q <= 0 or not math.isfinite(q):
        return 0.0
    d = min(8, max(0, 3-math.floor(math.log10(q))))
    f = 10**d
    return math.floor(q*f+1e-9)/f


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) and x > 0 else None
    except (TypeError, ValueError):
        return None


def _check(key, label, weight, status, value='', counter=''):
    return dict(key=key, label=label, weight=weight, status=status, value=value, counter=counter)


def _known(*values):
    return all(v is not None for v in values)


class View:
    """Direction-aware accessors so SHORT mirrors LONG exactly."""
    def __init__(self, s, side):
        self.s, self.side = s, side

    def __getitem__(self, k):
        return self.s.get(k)

    def level(self):  # structure the trade leans on
        return self.s.get('swing_low' if self.side == 1 else 'swing_high')

    def far(self):  # structure on the target side
        return self.s.get('swing_high' if self.side == 1 else 'swing_low')

    def recent(self):
        return self.s.get('recent_low' if self.side == 1 else 'recent_high')

    def prev_extreme(self):
        return self.s.get('prev_high' if self.side == 1 else 'prev_low')

    def bar_extreme(self):
        return self.s.get('high' if self.side == 1 else 'low')

    def above(self, a, b):  # "a is on the favourable side of b"
        return (a-b)*self.side > 0

    def reversal(self, key='patterns'):
        return bool(set(self.s.get(key) or []) & (BULL if self.side == 1 else BEAR))

    def div(self):
        return self.s.get('bull_div' if self.side == 1 else 'bear_div')


def _position_trigger(code_pos, code_trig, v, tf):
    """Returns (position_status, position_value, trigger_ok, trigger_price, wait_for, structure)."""
    atr, close, sd = v['atr'], v['close'], v.side
    word = '站上' if sd == 1 else '跌破'
    if code_pos == 'near_support':
        lvl, rec = v.level(), v.recent()
        if not _known(lvl, rec, atr, close):
            return 'unknown', 'K 线不足，找不到摆动点', False, None, None, None
        top = lvl+0.5*atr*sd
        near = not v.above(rec, top) and v.above(rec, lvl-1.0*atr*sd) or rec == lvl-1.0*atr*sd
        ok = not v.above(top, close)
        return ('pass' if near else 'fail', f'摆动{"低" if sd == 1 else "高"}点 {fmt(lvl)}，区域 {fmt(min(lvl, top))}–{fmt(max(lvl, top))}，最近 {fmt(rec)}',
                ok, top, None if ok else f'等 {tf} 收盘{word} {fmt(top)}（区域{"上" if sd == 1 else "下"}沿）', lvl)
    if code_pos == 'pullback_ema20':
        e20, e50, rec, prev = v['ema20'], v['ema50'], v.recent(), v.prev_extreme()
        if not _known(e20, e50, rec, atr, close, prev):
            return 'unknown', 'K 线不足，EMA 未形成', False, None, None, None
        near = not v.above(rec, e20+0.5*atr*sd) and not v.above(e50, close)
        ok = v.above(close, prev) and v.above(close, e20)
        trig = v.bar_extreme()
        return ('pass' if near else 'fail', f'EMA20 {fmt(e20)}，最近 {fmt(rec)}，EMA50 {fmt(e50)}',
                ok, trig, None if ok else f'等 {tf} 收盘{word}回踩 K 线的{"高" if sd == 1 else "低"}点 {fmt(trig)}，且在 EMA20 {"上" if sd == 1 else "下"}方', rec)
    if code_pos == 'oversold_at_low':
        r, rp, lvl, rec = v['rsi'], v['rsi_prev'], v.level(), v.recent()
        if not _known(r, lvl, rec, atr, close):
            return 'unknown', 'K 线不足，RSI 或摆动点未形成', False, None, None, None
        extreme = (lambda x: x is not None and (x < 30 if sd == 1 else x > 70))
        near = (extreme(r) or extreme(rp)) and not v.above(rec, lvl+1.0*atr*sd)
        prev = v.prev_extreme()
        ok = v.reversal('prev_patterns') and prev is not None and v.above(close, prev)
        if ok:
            return 'pass' if near else 'fail', f'RSI {r2(r)}，摆动点 {fmt(lvl)}', True, prev, None, rec
        if v.reversal():
            trig = v.bar_extreme()
            return ('pass' if near else 'fail', f'RSI {r2(r)}，摆动点 {fmt(lvl)}', False, trig,
                    f'反转 K 线已出现，等下一根{word}它的{"高" if sd == 1 else "低"}点 {fmt(trig)}', rec)
        return ('pass' if near else 'fail', f'RSI {r2(r)}，摆动点 {fmt(lvl)}', False, None,
                f'等一根反转 K 线收盘，再{word}它的{"高" if sd == 1 else "低"}点', rec)
    return None, '', None, None, None, None


def judge(plan, market, system, account):
    side = 1 if plan.get('direction') == 'LONG' else -1
    tf = plan.get('timeframe') or '5m'
    d, h = View(market.get('decision') or {}, side), View(market.get('higher') or {}, side)
    rules = system['rules']
    setups = {s['id']: s for s in system['setups']}
    setup = setups.get(plan.get('setup_id') or '')
    sid = setup['id'] if setup else ''
    entry, stop, target = _num(plan.get('entry_price')), _num(plan.get('stop_loss')), _num(plan.get('take_profit'))
    hard, lessons = [], []
    flow = market.get('flow') or {}
    atr = d['atr']

    # Location and trigger
    pos_status, pos_value, trig_ok, trig_price, wait_for, structure = None, '', None, None, None, None
    if setup and setup['position']:
        pos_status, pos_value, trig_ok, trig_price, wait_for, structure = _position_trigger(setup['position'][0], setup['trigger'][0], d, tf)
        if pos_status == 'fail':
            hard.append(f'还没到位：{setup["name"]}要求的位置条件未满足（{pos_value}）。')
    if sid == 'discretionary' and not (str(plan.get('bet') or '').strip() and str(plan.get('time_limit') or '').strip()):
        hard.append('盘感试单需要先写清楚：押什么反应、多久没出现就走。')
    if structure is None:
        structure = d.recent()

    # Levels
    dist = (entry-stop)*side if entry and stop else None
    if not stop or not entry or dist is None or dist <= 0:
        hard.append('止损缺失或方向不对：做多止损要低于入场，做空止损要高于入场。')
        dist = None
    suggested_stop = (structure-0.25*atr*side) if _known(structure, atr) else None
    min_rr = rules['min_rr']
    eff = dist or ((entry-suggested_stop)*side if entry and suggested_stop and (entry-suggested_stop)*side > 0 else None)
    levels = dict(stop=suggested_stop, stop_basis='结构位外 0.25 × ATR14（只是候选，未写入你的计划）' if suggested_stop else '数据不足，无法给出结构止损',
                  target=None, target_basis='')
    if entry and eff:
        far = d.far()
        if setup and setup['target_rule'] == 'rr_2':
            levels.update(target=entry+2*eff*side, target_basis='2R 目标')
        elif far is not None and (far-entry)*side >= min_rr*eff:
            levels.update(target=far, target_basis=f'前方摆动点 {fmt(far)}，约 {r2((far-entry)*side/eff)}R')
        else:
            levels.update(target=entry+min_rr*eff*side,
                          target_basis=f'最低 {r2(min_rr)}R' + (f'；前方摆动点 {fmt(far)} 只有 {r2((far-entry)*side/eff)}R，可能先受阻' if far is not None and (far-entry)*side > 0 else ''))
    rr = None
    if dist and target:
        reward = (target-entry)*side
        if reward <= 0:
            hard.append('止盈方向不对，请核对目标价。')
        else:
            rr = reward/dist
            if rr < min_rr:
                hard.append(f'盈亏比 {r2(rr)}R 低于你系统里的最低 {r2(min_rr)}R。')

    # Dimensions
    ctx = []
    if _known(h['ema20'], h['ema50']):
        ok = h.above(h['ema20'], h['ema50'])
        ctx.append(_check('higher_tf_trend', '高一级周期趋势', 12, 'pass' if ok else 'fail', f'EMA20 {fmt(h["ema20"])} / EMA50 {fmt(h["ema50"])}',
                          '' if ok else '高一级周期趋势和你的方向相反，这是逆势单。'))
    else:
        ctx.append(_check('higher_tf_trend', '高一级周期趋势', 12, 'unknown', '数据不足'))
    if _known(d['close'], d['ema50']):
        ok = d.above(d['close'], d['ema50'])
        ctx.append(_check('decision_trend', '决策周期在 EMA50 同侧', 8, 'pass' if ok else 'fail', f'收盘 {fmt(d["close"])} / EMA50 {fmt(d["ema50"])}',
                          '' if ok else '决策周期价格在 EMA50 另一侧，短线结构还没站到你这边。'))
    else:
        ctx.append(_check('decision_trend', '决策周期在 EMA50 同侧', 8, 'unknown', '数据不足'))
    rs = (market.get('relative') or {}).get('relative_strength') or {}
    if rs.get('status') == 'complete':
        ex = float(rs['excess_percentage_points']); ok = ex*side > 0
        ctx.append(_check('relative_strength', '相对 BTC 强弱', 5, 'pass' if ok else 'fail', f'{ex:+.2f} 个百分点',
                          '' if ok else '同期表现弱于（做空时强于）BTC。'))
    else:
        ctx.append(_check('relative_strength', '相对 BTC 强弱', 5, 'unknown', '未比较'))

    loc = []
    if setup and setup['position']:
        loc.append(_check('position', '到位条件', 15, pos_status, pos_value, '还没到你这类交易的位置。' if pos_status == 'fail' else ''))
    else:
        loc.append(_check('position', '到位条件', 15, 'unknown', '盘感或未归类：不检查位置'))
    if stop and structure is not None and atr:
        ok = (structure-stop)*side >= 0.25*atr*0.999
        loc.append(_check('stop_at_structure', '止损放在结构外', 10, 'pass' if ok else 'fail', f'止损 {fmt(stop)} / 结构 {fmt(structure)}',
                          '' if ok else '止损放在结构里面，正常回踩就可能扫掉。'))
    else:
        loc.append(_check('stop_at_structure', '止损放在结构外', 10, 'unknown', '缺少止损或结构'))

    mom = []
    has_bars = bool(d['bars'])
    rev = d.reversal() or d.reversal('prev_patterns')
    mom.append(_check('reversal_candle', '反转 K 线', 7, 'unknown' if not has_bars else 'pass' if rev else 'fail',
                      '、'.join(dict.fromkeys(PATTERN_NAMES.get(x, x) for x in (d['patterns'] or []) + (d['prev_patterns'] or []))) or '无',
                      '' if rev else '最近两根没有反转 K 线。'))
    if d['rsi'] is None:
        mom.append(_check('rsi', 'RSI', 5, 'unknown', '数据不足'))
    else:
        ok = bool(d.div()) or (d['rsi_prev'] is not None and (d['rsi']-d['rsi_prev'])*side > 0)
        mom.append(_check('rsi', 'RSI', 5, 'pass' if ok else 'fail', f'{r2(d["rsi"])}' + ('（背离）' if d.div() else ''),
                          '' if ok else 'RSI 还在往不利方向走，也没有背离。'))
    if _known(d['macd_hist'], d['macd_hist_prev']):
        ok = (d['macd_hist']-d['macd_hist_prev'])*side > 0
        mom.append(_check('macd', 'MACD 柱', 5, 'pass' if ok else 'fail', f'{fmt(d["macd_hist_prev"])} → {fmt(d["macd_hist"])}',
                          '' if ok else 'MACD 动能还在朝反方向扩大。'))
    else:
        mom.append(_check('macd', 'MACD 柱', 5, 'unknown', '数据不足'))
    if d['volume_ratio'] is None:
        mom.append(_check('volume', '成交量', 4, 'unknown', '数据不足'))
    else:
        ok = d['volume_ratio'] >= 1.5
        mom.append(_check('volume', '成交量', 4, 'pass' if ok else 'fail', f'{r2(d["volume_ratio"])} 倍均量', '' if ok else '没有放量确认。'))
    if flow.get('status') in ('complete', 'partial') and flow.get('taker_ratio_avg') is not None:
        if sid == 'exhaustion_reversal':
            ok = flow.get('taker_ratio_last') is not None and (flow['taker_ratio_last']-flow['taker_ratio_avg'])*side > 0
            value = f'主动买卖比 {r2(flow["taker_ratio_avg"])} → {r2(flow["taker_ratio_last"]) if flow.get("taker_ratio_last") is not None else "—"}'
        else:
            oi = flow.get('oi_change_pct')
            ok = (flow['taker_ratio_avg']-1)*side > 0 and (oi is None or oi > 0)
            value = f'主动买卖比 {r2(flow["taker_ratio_avg"])}，OI {"" if oi is None else f"{oi:+.2f}%"}'
        mom.append(_check('flow', 'Binance 资金面', 4, 'pass' if ok else 'fail', value, '' if ok else '主动成交和持仓量没有站在你这边。'))
    else:
        mom.append(_check('flow', 'Binance 资金面', 4, 'unknown', '未取得'))

    # Sizing
    caps = [x for x in (account.get('cap_amount'), (account['equity']*rules['account_risk_pct']/100) if account.get('equity') else None, account.get('budget')) if x]
    budget = min(caps) if caps else None
    full = floor_qty(budget/dist) if budget and dist else 0.0
    trial = floor_qty(full*rules['trial_fraction'])
    trial_mode = sid == 'discretionary' or (trig_ok is False and pos_status == 'pass')
    qty = trial if trial_mode else full
    risk = qty*dist if dist else None
    combined = (account.get('open_risk') or 0)+(risk or 0)
    combined_pct = combined/account['equity']*100 if account.get('equity') else None
    risk_checks = [_check('rr', '盈亏比', 12, 'unknown' if rr is None else 'pass' if rr >= min_rr else 'fail',
                          '—' if rr is None else f'{r2(rr)}R（最低 {r2(min_rr)}R）', '' if rr is None or rr >= min_rr else '赚的空间不够覆盖风险。')]
    if dist and atr:
        k = dist/atr; ok = 0.5 <= k <= 3
        risk_checks.append(_check('stop_atr', '止损距离', 6, 'pass' if ok else 'fail', f'{r2(k)} × ATR',
                                  '' if ok else '止损太近，正常波动就会扫到。' if k < 0.5 else '止损太远，仓位会被迫很小。'))
    else:
        risk_checks.append(_check('stop_atr', '止损距离', 6, 'unknown', '缺少止损或 ATR'))
    limit = rules['account_risk_pct']*COMBINED_MULTIPLE
    if combined_pct is None:
        risk_checks.append(_check('combined', '同方向合计风险', 7, 'unknown', '未填账户权益'))
    else:
        ok = combined_pct <= limit
        risk_checks.append(_check('combined', '同方向合计风险', 7, 'pass' if ok else 'fail', f'{r2(combined_pct)}%（上限 {r2(limit)}%）',
                                  '' if ok else '加上已有的同方向持仓，整体风险超过账户上限。'))
        if not ok:
            lessons.append('correlated_risk')

    dims = [dict(key='context', name='大背景', checks=ctx), dict(key='location', name='位置', checks=loc),
            dict(key='momentum', name='信号与动能', checks=mom), dict(key='risk', name='风险回报', checks=risk_checks)]
    for dim in dims:
        dim['max'] = 25
        dim['score'] = round(sum(c['weight']*(1 if c['status'] == 'pass' else .5 if c['status'] == 'unknown' else 0) for c in dim['checks']), 1)
    score = round(sum(dim['score'] for dim in dims))

    # Verdict
    if hard:
        state = 'no'
    elif pos_status == 'pass' and trig_ok is False:
        state = 'wait_trigger'
    elif pos_status == 'unknown':
        state = 'conditional'
        wait_for = wait_for or 'K 线数据不足，先在图上确认位置和触发。'
    elif score < 45:
        state = 'no'
    elif not setup or sid == 'discretionary':
        state = 'conditional'
    else:
        state = 'go' if score >= 70 else 'conditional'
    if state == 'conditional' and not wait_for:
        weak = [c for dim in dims for c in dim['checks'] if c['status'] == 'fail']
        wait_for = ('先补强：' + '；'.join(c['label'] for c in weak[:2])) if weak else None

    failed = sorted((c for dim in dims for c in dim['checks'] if c['status'] == 'fail'), key=lambda c: -c['weight'])
    unknown = sorted((c for dim in dims for c in dim['checks'] if c['status'] == 'unknown'), key=lambda c: -c['weight'])
    counter = failed[0]['counter'] if failed else (f'无法确认：{unknown[0]["label"]}（{unknown[0]["value"]}）' if unknown else None)

    if state == 'wait_trigger':
        lessons.insert(0, 'trigger_vs_location')
    if sid == 'discretionary':
        lessons.append('discretionary_limit')
    if plan.get('is_add'):
        lessons.append('add_recalc')
    if any(c['key'] == 'stop_atr' and c['status'] == 'fail' for c in risk_checks) or (_num(plan.get('quantity')) or 0) > full > 0:
        lessons.append('size_by_stop')
    if (account.get('cooldown') or {}).get('active'):
        lessons.insert(0, 'cooldown')

    return dict(state=state, label=LABELS[state], score=score, dimensions=dims, hard_fails=hard,
                position_ok=None if pos_status in (None, 'unknown') else pos_status == 'pass',
                trigger_ok=trig_ok, trigger_price=trig_price, wait_for=wait_for, counter=counter, levels=levels,
                sizing=dict(budget=budget, currency=account.get('currency'), cap_amount=account.get('cap_amount'),
                            cap_pct_amount=(account['equity']*rules['account_risk_pct']/100) if account.get('equity') else None,
                            equity=account.get('equity'), equity_source=account.get('equity_source'),
                            full_qty=full, trial_qty=trial, recommended='trial' if trial_mode else 'full', qty=qty,
                            risk=risk, combined_risk=combined, combined_pct=combined_pct, combined_limit_pct=limit,
                            note='未含手续费、资金费和滑点；数量向下取整，亏损不超过预算。'),
                flow=flow, lessons=list(dict.fromkeys(lessons)), draft=not rules.get('confirmed'),
                setup=dict(id=sid, name=setup['name'] if setup else '未归类'),
                rr=rr, timeframe=tf, note='规则判断，不是交易许可或盈利保证；最终下单由你决定。')
