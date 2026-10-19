import copy
import unittest

import setup_judge as sj
import trading_system as ts

SYSTEM = copy.deepcopy(ts.DEFAULT_SYSTEM)


def long_snap(**over):
    s = dict(bars=250, close=100.3, high=100.6, low=100.1, ema20=101.0, ema50=99.0, rsi=35.0, rsi_prev=33.0,
             macd_hist=-0.1, macd_hist_prev=-0.2, atr=1.0, volume_ratio=1.6, swing_low=100.0, swing_high=110.0,
             patterns=['hammer'], prev_patterns=[], bull_div=True, bear_div=False,
             prev_high=100.8, prev_low=100.2, recent_low=100.2, recent_high=100.9)
    s.update(over)
    return s


HIGHER_UP = dict(long_snap(), close=104.0, ema20=105.0, ema50=100.0)


def market(decision=None, higher=None, flow=None, relative=None):
    return dict(decision=decision or long_snap(), higher=higher or HIGHER_UP,
                relative=relative or {'relative_strength': {'status': 'unavailable'}},
                flow=flow or {'status': 'unavailable'})


def plan(**over):
    p = dict(direction='LONG', timeframe='5m', entry_price='100.3', stop_loss='99.6', take_profit='102',
             setup_id='support_bounce', bet='', time_limit='', quantity='', is_add=False)
    p.update(over)
    return p


ACCOUNT = dict(budget=None, cap_amount=200.0, currency='USD', equity=None, equity_source=None, open_risk=0.0,
               cooldown={'active': False})

PRICE = ('close', 'high', 'low', 'ema20', 'ema50', 'swing_low', 'swing_high', 'prev_high', 'prev_low', 'recent_low', 'recent_high')
SWAP = (('high', 'low'), ('prev_high', 'prev_low'), ('recent_low', 'recent_high'), ('swing_low', 'swing_high'), ('bull_div', 'bear_div'))
PAT = {'hammer': 'shooting_star', 'bull_engulfing': 'bear_engulfing', 'bull_pin': 'bear_pin'}
PAT.update({v: k for k, v in PAT.items()})


def mirror(s, pivot=200.0):
    m = dict(s)
    for k in PRICE:
        if s.get(k) is not None:
            m[k] = pivot - s[k]
    for a, b in SWAP:
        m[a], m[b] = m[b], m[a]
    for k in ('rsi', 'rsi_prev'):
        m[k] = 100 - s[k] if s.get(k) is not None else None
    for k in ('macd_hist', 'macd_hist_prev'):
        m[k] = -s[k] if s.get(k) is not None else None
    for k in ('patterns', 'prev_patterns'):
        m[k] = [PAT.get(x, x) for x in s[k]]
    return m


class JudgeTests(unittest.TestCase):
    def test_support_bounce_waits_for_trigger(self):
        j = sj.judge(plan(), market(), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'wait_trigger'); self.assertEqual(j['label'], '等触发')
        self.assertTrue(j['position_ok']); self.assertFalse(j['trigger_ok'])
        self.assertAlmostEqual(j['trigger_price'], 100.5)
        self.assertIn('100.5', j['wait_for']); self.assertIn('trigger_vs_location', j['lessons'])
        self.assertEqual(j['sizing']['recommended'], 'trial')
        self.assertTrue(j['draft'])

    def test_go_when_all_confirmed(self):
        snap = long_snap(close=100.7)
        j = sj.judge(plan(entry_price='100.7', take_profit='102.9'), market(snap), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'go'); self.assertGreaterEqual(j['score'], 70)
        self.assertEqual([d['key'] for d in j['dimensions']], ['context', 'location', 'momentum', 'risk'])
        self.assertTrue(all(d['max'] == 25 for d in j['dimensions']))
        self.assertEqual(j['sizing']['recommended'], 'full')

    def test_rr_below_min_is_hard_fail(self):
        j = sj.judge(plan(entry_price='100.7', take_profit='101.54'), market(long_snap(close=100.7)), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'no'); self.assertTrue(any('盈亏比' in x for x in j['hard_fails']))

    def test_position_not_reached_is_no(self):
        j = sj.judge(plan(entry_price='103'), market(long_snap(close=103, recent_low=102.6)), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'no'); self.assertFalse(j['position_ok'])
        self.assertTrue(any('还没到位' in x for x in j['hard_fails']))

    def test_budget_uses_smallest_cap(self):
        acct = dict(ACCOUNT, equity=1636.0, equity_source='截图')
        j = sj.judge(plan(entry_price='100.7'), market(long_snap(close=100.7)), SYSTEM, acct)
        self.assertAlmostEqual(j['sizing']['budget'], 32.72)
        self.assertAlmostEqual(j['sizing']['full_qty'], 29.74)  # 32.72 / 1.1 floored to 4 significant digits
        self.assertAlmostEqual(j['sizing']['trial_qty'], 14.87)
        j = sj.judge(plan(entry_price='100.7'), market(long_snap(close=100.7)), SYSTEM, dict(acct, budget=20.0))
        self.assertAlmostEqual(j['sizing']['budget'], 20.0)

    def test_discretionary_requires_bet_and_limit_and_trial(self):
        j = sj.judge(plan(setup_id='discretionary'), market(), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'no'); self.assertTrue(any('押什么' in x for x in j['hard_fails']))
        j = sj.judge(plan(setup_id='discretionary', bet='5m 放量收回', time_limit='6 根 K 线'), market(), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'conditional'); self.assertEqual(j['sizing']['recommended'], 'trial')
        self.assertIn('discretionary_limit', j['lessons'])

    def test_combined_risk_counts_open_positions(self):
        acct = dict(ACCOUNT, equity=1636.0, budget=30.0, open_risk=150.0)
        j = sj.judge(plan(entry_price='100.7', take_profit='102.9'), market(long_snap(close=100.7)), SYSTEM, acct)
        self.assertAlmostEqual(j['sizing']['combined_pct'], (150 + j['sizing']['risk'])/1636*100)
        self.assertGreater(j['sizing']['combined_pct'], 10.9)
        self.assertIn('correlated_risk', j['lessons'])

    def test_flow_bonus_for_strength_follow(self):
        snap = long_snap(close=101.2, recent_low=100.8, prev_high=101.1)
        good = {'status': 'complete', 'oi_change_pct': 2.0, 'taker_ratio_avg': 1.3, 'taker_ratio_last': 1.4}
        p = plan(setup_id='strength_follow', entry_price='101.2', stop_loss='100.4', take_profit='103')
        flow_check = lambda j: next(c for d in j['dimensions'] for c in d['checks'] if c['key'] == 'flow')
        self.assertEqual(flow_check(sj.judge(p, market(snap, flow=good), SYSTEM, ACCOUNT))['status'], 'pass')
        self.assertEqual(flow_check(sj.judge(p, market(snap), SYSTEM, ACCOUNT))['status'], 'unknown')

    def test_short_mirrors_long(self):
        j = sj.judge(plan(direction='SHORT', entry_price='99.7', stop_loss='100.4', take_profit='98'),
                     market(mirror(long_snap()), mirror(HIGHER_UP)), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'wait_trigger'); self.assertAlmostEqual(j['trigger_price'], 99.5)
        go = sj.judge(plan(direction='SHORT', entry_price='99.3', stop_loss='100.4', take_profit='97.1'),
                      market(mirror(long_snap(close=100.7)), mirror(HIGHER_UP)), SYSTEM, ACCOUNT)
        self.assertEqual(go['state'], 'go')

    def test_bad_stop_and_tiny_prices(self):
        j = sj.judge(plan(stop_loss='101'), market(), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'no'); self.assertTrue(any('止损' in x for x in j['hard_fails']))
        j = sj.judge(plan(stop_loss=''), market(), SYSTEM, ACCOUNT)
        self.assertEqual(j['state'], 'no')
        tiny = {k: (v*1e-7 if isinstance(v, float) and k in PRICE + ('atr',) else v) for k, v in long_snap(close=100.7).items()}
        j = sj.judge(plan(entry_price='0.00001007', stop_loss='0.00000996', take_profit='0.00001029'),
                     market(tiny, {k: (v*1e-7 if isinstance(v, float) and k in PRICE + ('atr',) else v) for k, v in HIGHER_UP.items()}),
                     SYSTEM, dict(ACCOUNT, budget=100.0))
        self.assertEqual(j['state'], 'go')
        self.assertLessEqual(j['sizing']['risk'], 100.0); self.assertGreater(j['sizing']['risk'], 99.0)
        self.assertEqual(sj.fmt(0.00001234), '0.00001234')

    def test_unknown_inputs_stay_unknown(self):
        empty = {k: None for k in long_snap()}; empty.update(bars=0, patterns=[], prev_patterns=[])
        j = sj.judge(plan(), market(empty, empty), SYSTEM, ACCOUNT)
        self.assertNotEqual(j['state'], 'go')
        statuses = {c['status'] for d in j['dimensions'] if d['key'] != 'risk' for c in d['checks']}
        self.assertEqual(statuses, {'unknown'})

    def test_prices_round_to_instrument_decimals(self):
        j = sj.judge(plan(), market(long_snap(atr=0.3333, close=100.15, recent_low=100.1, price_decimals=2)), SYSTEM, ACCOUNT)
        self.assertEqual(j['trigger_price'], 100.17); self.assertIn('100.17', j['wait_for'])
        self.assertEqual(j['levels']['stop'], 99.92)

    def test_cooldown_adds_lesson_not_hard_fail(self):
        acct = dict(ACCOUNT, cooldown={'active': True})
        j = sj.judge(plan(entry_price='100.7', take_profit='102.9'), market(long_snap(close=100.7)), SYSTEM, acct)
        self.assertEqual(j['state'], 'go'); self.assertIn('cooldown', j['lessons'])


if __name__ == '__main__':
    unittest.main()
