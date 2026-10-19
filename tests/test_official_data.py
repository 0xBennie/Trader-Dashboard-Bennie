import unittest

import official_data as od

ANCHOR=1_800_000_000_000
STEP=300000


def fake(url,params):
    ts=[ANCHOR-STEP*i for i in range(12,-2,-1)]  # last sample is after the anchor
    if url.endswith('/premiumIndex'):
        return {'symbol':'NEARUSDT','markPrice':'4.98','lastFundingRate':'0.0001','time':ANCHOR}
    if url.endswith('/takerlongshortRatio'):
        return [{'buySellRatio':'2.0' if t>ANCHOR else '1.2','timestamp':t} for t in ts]
    if url.endswith('/topLongShortPositionRatio'):
        return [{'symbol':'NEARUSDT','longShortRatio':'9' if t>ANCHOR else '2.5','timestamp':t} for t in ts]
    if url.endswith('/openInterestHist'):
        return [{'symbol':'NEARUSDT','sumOpenInterest':str(1000+i*10),'timestamp':t} for i,t in enumerate(ts)]
    raise AssertionError(url)


class FlowTests(unittest.TestCase):
    def test_flow_reads_all_sources(self):
        f=od.binance_flow('NEARUSDT','5m',ANCHOR,getter=fake)
        self.assertEqual(f['status'],'complete');self.assertEqual(f['period'],'5m')
        self.assertEqual(f['mark_price'],4.98);self.assertEqual(f['funding_rate'],0.0001)
        self.assertEqual(f['taker_ratio_last'],1.2);self.assertAlmostEqual(f['taker_ratio_avg'],1.2)
        self.assertEqual(f['top_long_short'],2.5)
        self.assertAlmostEqual(f['oi_change_pct'],(1120/1010-1)*100)
        self.assertLessEqual(f['through'],ANCHOR)

    def test_flow_ignores_future_samples(self):
        f=od.binance_flow('NEARUSDT','5m',ANCHOR,getter=fake)
        self.assertNotEqual(f['taker_ratio_last'],2.0);self.assertNotEqual(f['top_long_short'],9.0)

    def test_unsupported_period_maps_to_nearest(self):
        self.assertEqual(od.binance_flow('NEARUSDT','3m',ANCHOR,getter=fake)['period'],'5m')

    def test_failures_are_explicit(self):
        def boom(url,params):raise OSError('down')
        self.assertEqual(od.binance_flow('NEARUSDT','5m',ANCHOR,getter=boom)['status'],'unavailable')
        def partial(url,params):
            if url.endswith('/premiumIndex'):raise OSError('down')
            if url.endswith('/takerlongshortRatio'):return {'code':-1}
            return fake(url,params)
        f=od.binance_flow('NEARUSDT','5m',ANCHOR,getter=partial)
        self.assertEqual(f['status'],'partial');self.assertIsNone(f['mark_price']);self.assertIsNone(f['taker_ratio_last'])
        self.assertEqual(f['top_long_short'],2.5)

    def test_public_get_allows_flow_endpoints(self):
        import market_replay
        with self.assertRaises(ValueError):market_replay.public_get('https://example.com/x',{})
        for path in od.PATHS.values():
            self.assertTrue(path.startswith('https://fapi.binance.com/'))


if __name__=='__main__':
    unittest.main()
