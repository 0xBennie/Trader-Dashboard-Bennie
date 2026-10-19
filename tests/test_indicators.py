import unittest

import indicators as ind


def bar(o,h,l,c,v=10):
    return dict(open=str(o),high=str(h),low=str(l),close=str(c),volume=str(v))


class IndicatorTests(unittest.TestCase):
    def test_ema_rsi_macd_known_values(self):
        closes=[float(i) for i in range(1,31)]
        e=ind.ema(closes,20)
        self.assertIsNone(e[18]);self.assertAlmostEqual(e[19],sum(closes[:20])/20,9)
        r=ind.rsi(closes,14)
        self.assertIsNone(r[13]);self.assertEqual(r[-1],100.0)
        m=ind.macd([float(i) for i in range(1,60)])
        self.assertGreater(m['line'][-1],0);self.assertIsNotNone(m['hist'][-1])

    def test_atr_wilder(self):
        candles=[bar(10,11,9,10)]*20
        self.assertAlmostEqual(ind.atr(candles,14)[-1],2.0,9)

    def test_patterns_detect_hammer_engulfing(self):
        hammer=[bar(10,10.5,9.5,10)]*3+[bar(10,10.12,8.8,10.1)]
        self.assertIn('hammer',ind.patterns(hammer))
        engulf=[bar(10,10.5,9.5,10)]*3+[bar(10,10.1,9.6,9.7),bar(9.65,10.3,9.6,10.2)]
        self.assertIn('bull_engulfing',ind.patterns(engulf))
        shooting=[bar(10,10.5,9.5,10)]*3+[bar(10,11.2,9.9,9.95)]
        self.assertIn('shooting_star',ind.patterns(shooting))

    def test_swings_need_right_side_confirmation(self):
        lows=[10,9,8,9,10,9.5,7.5,8.5]
        candles=[bar(l+1,l+2,l,l+1) for l in lows]
        s=ind.swings(candles)
        self.assertEqual([i for i,_ in s['lows']],[2])  # index 6 lacks two confirming bars

    def test_bull_divergence(self):
        # price: lower low at the second swing; rsi: higher low
        candles=[bar(l+1,l+2,l,l+1) for l in [12,11,10,11,12,11.5,9.5,10.5,11.5]]
        rsi=[50,40,25,40,50,45,30,40,50]
        self.assertTrue(ind.divergence(candles,rsi,'bull'))
        self.assertFalse(ind.divergence(candles,[50,40,30,40,50,45,25,40,50],'bull'))

    def test_short_history_returns_none(self):
        s=ind.snapshot([bar(10,11,9,10)]*10)
        self.assertEqual(s['bars'],10)
        for k in ('ema50','rsi','macd_hist','atr','volume_ratio'):
            self.assertIsNone(s[k])
        self.assertIsNone(ind.snapshot([])['close'])

    def test_snapshot_exposes_previous_and_recent_bars(self):
        candles=[bar(10,11,9,10),bar(10,12,8.5,11),bar(11,11.5,10,10.5),bar(10,10.12,8.8,10.1)]
        s=ind.snapshot(candles)
        self.assertEqual((s['prev_high'],s['prev_low']),(11.5,10.0))
        self.assertEqual((s['recent_low'],s['recent_high']),(8.5,12.0))
        self.assertIn('hammer',s['patterns']);self.assertEqual(s['prev_patterns'],ind.patterns(candles[:-1]))


if __name__=='__main__':
    unittest.main()
