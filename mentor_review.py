"""Post-close mentor review: was the idea right, was the plan followed? Rules only; personal notes stay the trader's."""
import json
import uuid

import journal
import review_automation
import review_learning

QUADRANTS = {'right_clean': '判断对，执行好', 'good_loss': '判断错，执行好（好的亏损）',
             'clean_unknown': '执行好（判断无法核对）', 'lucky': '运气赚钱：结果好，过程有问题',
             'both': '判断或执行有问题'}
PRIORITY = ['ignored_verdict', 'cooldown', 'no_stop', 'size_by_stop', 'add_recalc', 'early_exit']
NEXT = {'ignored_verdict': '开仓前判断是「别做」或「等触发」时，不下单；等触发出现再按三步重新检查。',
        'cooldown': '止损后冷静期内不开新仓，先写一句复盘，期满再检查。',
        'no_stop': '开仓同时挂好止损，没有止损不下单。',
        'size_by_stop': '下单前用卡片按止损算数量，计划亏损不超过上限。',
        'add_recalc': '加仓前勾选「这是加仓」，按整笔仓位重算风险。',
        'early_exit': '提前离场前写下看到的失效信号；没有信号就按原止损止盈走。'}
HABITS = {'early_exit': '没到止损也没到止盈就提前离场', 'no_stop': '没有止损', 'cooldown': '亏损后冷静期内又开仓',
          'size_by_stop': '计划亏损超过上限', 'add_recalc': '临时加仓没重算风险', 'ignored_verdict': '无视开仓前判断'}
LESSON = {'ignored_verdict': 'trigger_vs_location', 'cooldown': 'cooldown', 'no_stop': 'size_by_stop',
          'size_by_stop': 'size_by_stop', 'add_recalc': 'add_recalc', 'early_exit': 'early_exit'}


def _f(v):
    try:
        x = float(v)
        return x if x == x and abs(x) != float('inf') else None
    except (TypeError, ValueError):
        return None


def _item(code, label, status, value=''):
    return dict(code=code, label=label, status=status, value=value)


def assess(record, replay, check, ctx):
    closed = review_learning.closed(record)
    if not closed:
        return dict(status='not_closed', note='确认平仓后才做导师复盘。')
    side = 1 if record.get('direction') == 'LONG' else -1
    opened = review_learning.opened(record)
    ex = record.get('execution') or {}
    entry = _f(ex.get('entry_price')) or _f(record.get('entry_price'))
    qty = _f(record.get('quantity')) or _f(ex.get('entry_quantity'))
    plan = (check or {}).get('plan') or {}
    stop = _f(record.get('stop_loss')) or _f(plan.get('stop_loss'))
    target = _f(record.get('take_profit')) or _f(plan.get('take_profit'))
    res = review_automation.result(record)
    pnl = _f(res['net'] if res['net'] is not None else res['realized'])
    exit_price = _f((record.get('browser_close') or {}).get('close_price'))
    if exit_price is None and entry and pnl is not None:
        q = _f(ex.get('exit_quantity')) or qty
        realized = _f(ex.get('realized_pnl'))
        if q and realized is not None:
            exit_price = entry+realized/(q*side)
    risk = (entry-stop)*side if entry and stop and (entry-stop)*side > 0 else None

    # Was the idea right? First of planned stop / target after entry, on closed reference candles.
    judgment = dict(state='unknown', first_hit=None, mfe_r=None, mae_r=None, note='')
    if not opened or (replay or {}).get('anchor_kind') != 'entry':
        judgment['note'] = '入场时间未知：不判断先到止损还是止盈，也不计算浮盈浮亏。'
    elif not risk or not target:
        judgment['note'] = '缺少计划止损或止盈，无法判断这笔想法本身对不对。'
    else:
        frame = next((w for w in replay['windows'] if w.get('kind') == 'entry'), None)
        candles = sorted([c for c in (frame or {}).get('candles', []) if c['close_time'] >= opened],
                         key=lambda c: c['time'])
        if not candles:
            judgment['note'] = '入场后的参考 K 线没有取到。'
        for c in candles:
            hi, lo = float(c['high']), float(c['low'])
            fav, adv = (hi-entry if side == 1 else entry-lo), (entry-lo if side == 1 else hi-entry)
            if c['close_time'] <= closed:
                judgment['mfe_r'] = max(judgment['mfe_r'] or 0, fav/risk)
                judgment['mae_r'] = max(judgment['mae_r'] or 0, adv/risk)
            hit_stop = (lo <= stop) if side == 1 else (hi >= stop)
            hit_target = (hi >= target) if side == 1 else (lo <= target)
            if judgment['first_hit'] is None and (hit_stop or hit_target):
                judgment['first_hit'] = 'ambiguous' if hit_stop and hit_target else 'stop' if hit_stop else 'target'
        judgment['state'] = {'target': 'right', 'stop': 'wrong'}.get(judgment['first_hit'], 'unknown')
        if judgment['first_hit'] == 'ambiguous':
            judgment['note'] = '同一根 K 线同时碰到止损和止盈，无法判断先后。'
        elif candles and judgment['first_hit'] is None:
            judgment['note'] = '已取得的 K 线里止损和止盈都没被碰到。'
        else:
            judgment['note'] = 'Binance 参考 K 线，不是成交证据；包含平仓后的走势，用来判断想法本身。'

    # Did execution follow the plan?
    items = []
    cs = ((check or {}).get('judgment') or {}).get('state')
    items.append(_item('ignored_verdict', '尊重开仓前判断', 'unknown' if not check else 'fail' if cs in ('no', 'wait_trigger') else 'pass',
                       '没有关联的开仓前判断' if not check else f'当时判断：{ {"go":"可以做","conditional":"有条件做","wait_trigger":"等触发","no":"别做"}.get(cs, "未完成") }'))
    window = ctx.get('cooldown_minutes', 30)*60000
    prior = [t for t in ctx.get('losses', []) if opened and 0 <= opened-t < window]
    items.append(_item('cooldown', '不在冷静期内开仓', 'unknown' if not opened else 'fail' if prior else 'pass',
                       f'上一笔亏损后 {round((opened-max(prior))/60000)} 分钟就开仓' if prior else ''))
    items.append(_item('no_stop', '有止损', 'pass' if stop else 'fail', '' if stop else '记录里没有止损'))
    cap = ctx.get('cap_amount')
    planned = risk*qty if risk and qty else None
    items.append(_item('size_by_stop', '计划亏损在上限内', 'unknown' if planned is None or not cap else 'fail' if planned > cap*1.001 else 'pass',
                       '' if planned is None else f'计划亏损约 {planned:.2f}' + (f'，上限 {cap:g}' if cap else '')))
    events = ex.get('events')
    if isinstance(events, list) and events:
        opens = [e.get('order_key') for e in events if e.get('kind') in ('open', 'add')]
        extra = len(set(opens)) - 1
        items.append(_item('add_recalc', '没有临时加仓', 'fail' if extra > 0 else 'pass', f'加仓 {extra} 次' if extra > 0 else ''))
    else:
        items.append(_item('add_recalc', '没有临时加仓', 'unknown', '这个交易所的加仓记录无法核对'))
    if exit_price is None or not risk:
        items.append(_item('early_exit', '按止损或止盈离场', 'unknown', '缺少离场价或止损'))
    else:
        near = abs(exit_price-stop) <= 0.1*risk or (target is not None and abs(exit_price-target) <= 0.1*risk)
        items.append(_item('early_exit', '按止损或止盈离场', 'pass' if near else 'fail',
                           f'离场 {exit_price:.6g}，止损 {stop:.6g}' + (f'，止盈 {target:.6g}' if target else '') +
                           ('' if near else '（主动离场或移动止损）')))

    fails = [i['code'] for i in items if i['status'] == 'fail']
    clean = not fails
    if clean and judgment['state'] == 'right':
        quadrant = 'right_clean'
    elif clean and judgment['state'] == 'wrong':
        quadrant = 'good_loss'
    elif clean:
        quadrant = 'clean_unknown'
    elif pnl is not None and pnl > 0:
        quadrant = 'lucky'
    else:
        quadrant = 'both'
    top = next((c for c in PRIORITY if c in fails), None)
    return dict(status='complete', journal_id=record.get('id'), closed_at=closed, judgment=judgment, execution=items,
                quadrant=quadrant, quadrant_label=QUADRANTS[quadrant],
                next_action=dict(code=top, instruction=NEXT[top]) if top else None,
                lessons=[LESSON[c] for c in PRIORITY if c in fails] or (['good_loss'] if quadrant == 'good_loss' else []),
                pnl=pnl, pnl_asset=res.get('asset'), exit_price=exit_price, entry_price=entry, stop=stop, target=target,
                check_id=(check or {}).get('id'), check_state=cs, reference_only=record.get('exchange', 'binance') != 'binance',
                note='规则复盘：分开评价想法与执行，不代替你的个人复盘，也不由盈亏推断能力。')


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS mentor_reviews(journal_id TEXT PRIMARY KEY,closed INTEGER NOT NULL,updated INTEGER NOT NULL,data TEXT NOT NULL)')


def _base(symbol):
    return str(symbol or '').upper().removesuffix('USDT')


def _check(db, record, anchor):
    rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM assistant_checks ORDER BY created DESC')]
    wanted = record.get('pretrade_check_id')
    if wanted:
        found = next((c for c in rows if c.get('id') == wanted), None)
        return (found, 'manual') if found else (None, None)
    for c in rows:
        p = c.get('plan') or {}
        if (_base(p.get('symbol')) == _base(record.get('symbol')) and p.get('direction') == record.get('direction')
                and anchor and 0 <= anchor-c.get('created_at', 0) <= 2*3600000):
            return c, 'auto'
    return None, None


def _losses(db, records, before):
    out = []
    for r in records:
        t = review_learning.closed(r)
        if not t or not before or t >= before:
            continue
        res = review_automation.result(r)
        v = _f(res['net'] if res['net'] is not None else res['realized'])
        if v is not None and v < 0:
            out.append(t)
    return out


def _fresh(row, record, closed, replay):
    return bool(row and row['closed'] == closed and row['updated'] == record['updated']
                and json.loads(row['data']).get('replay_collected_at') == (replay or {}).get('collected_at'))


def get(jid, capture=True):
    import market_replay
    import trader_memory
    replay = market_replay.latest(jid)
    with journal.connect() as db:
        records = journal.all_journals(db)
        record = next((r for r in records if r['id'] == jid), None)
        if not record:
            raise ValueError('找不到这条交易记录。')
        closed = review_learning.closed(record)
        if not closed:
            return dict(status='not_closed', note='确认平仓后才做导师复盘。')
        row = db.execute('SELECT * FROM mentor_reviews WHERE journal_id=?', (jid,)).fetchone()
        if _fresh(row, record, closed, replay):
            return json.loads(row['data'])
        anchor = review_learning.opened(record) or record.get('created')
        check, link = _check(db, record, anchor)
        losses = _losses(db, records, review_learning.opened(record))
        minutes = int(journal.kv('assistant.cooldown', {}, db).get('minutes', 30))
    if not replay and capture:
        try:
            replay = market_replay.capture(jid)
        except Exception:
            replay = None
    pref = trader_memory.risk_preference(trader_memory.context(record.get('symbol')))
    result = assess(record, replay, check, dict(losses=losses, cooldown_minutes=minutes,
                                                cap_amount=float(pref['risk_budget_upper']) if pref else None))
    import lessons
    result.update(link=link, lesson_cards=lessons.pick(result.get('lessons', [])), replay_collected_at=(replay or {}).get('collected_at'))
    with journal.connect() as db:
        db.execute('INSERT OR REPLACE INTO mentor_reviews VALUES(?,?,?,?)', (jid, closed, record['updated'], json.dumps(result, ensure_ascii=False)))
    return result


def adopt(jid):
    review = get(jid)
    action = review.get('next_action')
    if not action:
        raise ValueError('这笔没有需要改进的执行问题，不用新增提醒。')
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM review_actions WHERE journal_id=? AND instruction=?', (jid, action['instruction'])).fetchone()
        if existing:
            db.execute('UPDATE review_actions SET archived=0 WHERE id=?', (existing['id'],))
            return dict(existing, archived=0)
        item = dict(id=uuid.uuid4().hex, journal_id=jid, instruction=action['instruction'], created=journal.now_ms(), archived=0)
        db.execute('INSERT INTO review_actions VALUES(:id,:journal_id,:instruction,:created,:archived)', item)
        return item


def recent_codes(n=10):
    counts = {}
    with journal.connect() as db:
        for (data,) in db.execute('SELECT data FROM mentor_reviews ORDER BY closed DESC LIMIT ?', (n,)):
            for item in json.loads(data).get('execution', []):
                if item['status'] == 'fail':
                    counts[item['code']] = counts.get(item['code'], 0)+1
    return counts


def lesson_order(lessons):
    """Cards for problems that keep coming back first; a repeated problem gets a card even if today's check missed it."""
    weight = {}
    for code, count in recent_codes().items():
        weight[LESSON[code]] = weight.get(LESSON[code], 0)+count
    ordered = sorted(dict.fromkeys(lessons), key=lambda c: -weight.get(c, 0))
    extra = [c for c, w in sorted(weight.items(), key=lambda x: -x[1]) if w >= 2 and c not in ordered]
    return ordered+extra


def warm_one(now=None):
    """Fill one missing or outdated review per worker turn from stored replays only; never fetches market data."""
    import market_replay
    with journal.connect() as db:
        records = [r for r in journal.all_journals(db) if review_learning.closed(r)]
        rows = {r['journal_id']: r for r in db.execute('SELECT * FROM mentor_reviews')}
    for record in sorted(records, key=lambda r: -review_learning.closed(r)):
        if _fresh(rows.get(record['id']), record, review_learning.closed(record), market_replay.latest(record['id'])):
            continue
        try:
            get(record['id'], capture=False)
        except Exception:
            continue  # One unreadable record must not starve the rest.
        return record['id']
    return None
