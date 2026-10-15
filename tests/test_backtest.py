import copy
import importlib.util
import json
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import journal

SPEC = importlib.util.find_spec('backtest')
if SPEC:
    import backtest

HOUR = 3600000
START = 1700002800000


def dataset(count=120):
    bars = [[START+i*HOUR, '100', '101', '99', '100', '10', START+(i+1)*HOUR-1]
            for i in range(count)]
    return dict(source='Binance USDⓈ-M public REST', symbol='ETHUSDT', interval='1h',
                bars=bars, funding=[], funding_error=None, retrieved_at=START+(count+1)*HOUR,
                requested_start=START, requested_end=START+count*HOUR-1)


def breakout_data():
    data = dataset()
    data['bars'][24][1:5] = ['100', '104', '99', '103']
    data['bars'][25][1:5] = ['104', '120', '90', '100']
    return data


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SPEC:
            raise AssertionError('The transparent backtest module has not been implemented.')

    def config(self, **kwargs):
        return backtest.normalize_payload(dict(symbol='ETHUSDT', strategy_id='breakout', **kwargs))

    def test_signal_uses_closed_bar_and_next_open_stop_first(self):
        data = breakout_data()
        result = backtest.simulate(data, self.config(fee_bps=0, slippage_bps=0))
        first = result['trades'][0]
        self.assertEqual((first['signal_index'], first['entry_index']), (24,25))
        self.assertEqual(Decimal(first['entry_price']), Decimal('104'))
        self.assertEqual(first['exit_reason'], 'stop')
        self.assertTrue(first['ambiguous_intrabar'])
        self.assertEqual(Decimal(first['gross_r']), Decimal('-1'))
        self.assertLess(first['signal_time'], first['entry_time'])
        changed = copy.deepcopy(data)
        for b in changed['bars'][30:]:
            b[1:5] = ['500', '900', '100', '600']
        future = backtest.simulate(changed, self.config(fee_bps=0, slippage_bps=0))
        self.assertEqual(first, future['trades'][0])

    def test_short_breakout_and_support_reclaim_mirror(self):
        data = breakout_data()
        for b in data['bars']:
            o,h,l,c = map(Decimal,b[1:5])
            b[1:5] = [str(200-o), str(200-l), str(200-h), str(200-c)]
        first = backtest.simulate(data, self.config(direction='SHORT',fee_bps=0,slippage_bps=0))['trades'][0]
        self.assertEqual(first['entry_price'], '96')
        self.assertEqual(first['exit_reason'], 'stop')
        data = dataset()
        data['bars'][24][1:5] = ['100','101','98','100']
        cfg = dict(self.config(fee_bps=0,slippage_bps=0), strategy_id='support_reclaim')
        trade = backtest.simulate(data,cfg)['trades'][0]
        self.assertEqual(trade['signal_index'],24)
        self.assertEqual(trade['signal_level'],'99')
        self.assertLess(Decimal(trade['stop_loss']),Decimal('98'))

    def test_boundary_forces_exit_and_signal_cannot_cross(self):
        data = dataset(100)
        data['bars'][58][1:5] = ['100','102','99','102']
        data['bars'][59][1:5] = ['102','102','101','102']
        trades = backtest.simulate(data,self.config(fee_bps=0,slippage_bps=0))['trades']
        self.assertEqual(trades[0]['exit_reason'],'boundary')
        self.assertEqual(trades[0]['exit_index'],59)
        for t in trades:
            lo,hi = {'development':(0,60),'validation':(60,80),'holdout':(80,100)}[t['split']]
            self.assertGreaterEqual(t['signal_index'],lo)
            self.assertLess(t['exit_index'],hi)

    def test_gap_fills_at_worse_open_with_slippage_and_ledger_reconciles(self):
        data = breakout_data()
        data['bars'][25][1:5] = ['104','105','103','104']
        data['bars'][26][1:5] = ['90','91','89','90']
        data['funding'] = [dict(symbol='ETHUSDT',fundingTime=START+26*HOUR-10,
                                fundingRate='0.001',markPrice='104')]
        t = backtest.simulate(data,self.config(fee_bps=5,slippage_bps=2))['trades'][0]
        self.assertEqual(t['exit_reason'],'stop_gap')
        self.assertEqual(Decimal(t['exit_price']),Decimal('89.982'))
        q=Decimal(t['quantity']); gross=Decimal(t['gross_pnl_usdt'])
        self.assertGreater(Decimal(t['slippage_usdt']),0)
        self.assertAlmostEqual(gross,(Decimal(t['exit_price'])-Decimal(t['entry_price']))*q,places=8)
        self.assertAlmostEqual(Decimal(t['fees_usdt']),(Decimal(t['entry_price'])+Decimal(t['exit_price']))*q*Decimal('.0005'),places=8)
        self.assertAlmostEqual(Decimal(t['net_pnl_usdt']),gross-Decimal(t['fees_usdt'])-Decimal(t['funding_cost_usdt']),places=8)
        self.assertGreater(Decimal(t['funding_cost_usdt']),0)

    def test_exit_bar_funding_credits_are_not_fabricated(self):
        data = breakout_data()
        data['funding'] = [dict(symbol='ETHUSDT',fundingTime=START+25*HOUR+10,
                                fundingRate='-0.001',markPrice='104')]
        t = backtest.simulate(data,self.config())['trades'][0]
        self.assertEqual(t['funding_cost_usdt'],'0')
        self.assertFalse(t['funding_events'][0]['applied'])

    def test_quality_rejects_missing_bar_duplicate_and_unclosed(self):
        for change in ('gap','duplicate','unclosed','bad_ohlc','nonfinite'):
            data=dataset()
            if change=='gap': del data['bars'][20]
            elif change=='duplicate': data['bars'][20]=copy.deepcopy(data['bars'][19])
            elif change=='unclosed': data['retrieved_at']=data['bars'][-1][6]
            elif change=='bad_ohlc': data['bars'][20][2]='90'
            else: data['bars'][20][2]='NaN'
            with self.subTest(change=change), self.assertRaises(backtest.DataQualityError):
                backtest.validate_data(data)

    def test_pf_infinity_and_wilson_are_not_zero_or_certain(self):
        trades=[dict(net_r='1',gross_r='1',net_pnl_usdt='100',gross_pnl_usdt='100',
                     fees_usdt='0',slippage_usdt='0',funding_cost_usdt='0')]*3
        result=backtest.metrics(trades)
        self.assertEqual(result['profit_factor'],'infinity')
        self.assertLess(Decimal(result['win_rate_ci']['lower']),1)
        self.assertGreater(Decimal(result['win_rate_ci']['lower']),0)
        self.assertIsNone(backtest.metrics([])['profit_factor'])

    def test_rejects_unversioned_parameter_tuning(self):
        with self.assertRaises(ValueError): self.config(params={'lookback':4})

    def test_invalid_next_open_cannot_create_a_position(self):
        data=breakout_data()
        data['bars'][25][1:5]=['90','95','89','90']
        result=backtest.simulate(data,self.config())
        self.assertFalse(any(t['signal_index']==24 for t in result['trades']))
        self.assertGreater(result['skipped_invalid_open'],0)

    def test_negative_validation_cannot_be_called_stable_support(self):
        good=dict(trade_count=40,net_expectancy_r='0.1')
        report=dict(data=dict(bar_count=2160,funding_coverage='available'),summary=copy.deepcopy(good),
                    splits=[dict(label=n,summary=dict(trade_count=15,net_expectancy_r=r))
                            for n,r in [('开发','0.3'),('验证','-0.1'),('留出','0.2')]],
                    cost_sensitivity=[{},dict(summary=good)])
        backtest._assess(report)
        self.assertEqual(report['summary']['evidence_status'],'not_supported')

    def test_public_download_uses_full_pagination_and_archives_raw_input(self):
        data=dataset(2160)
        now=data['requested_end']+1+5000
        calls=[]
        def source(client,path,params=None):
            calls.append((path,params))
            if path.endswith('exchangeInfo'):
                return dict(symbols=[dict(symbol='BTCUSDT',contractType='PERPETUAL',quoteAsset='USDT',marginAsset='USDT')])
            if path.endswith('/time'): return dict(serverTime=now)
            if path.endswith('/klines'):
                return [b for b in data['bars'] if params['startTime'] <= b[0] <= params['endTime']][:params['limit']]
            return []
        with tempfile.TemporaryDirectory() as tmp, patch.object(backtest,'RESEARCH',Path(tmp)), patch.object(backtest,'_public_get',side_effect=source):
            cfg=dict(self.config(),symbol='BTCUSDT')
            result,path,digest=backtest.load_data(cfg)
            self.assertEqual(len(result['bars']),2160)
            self.assertEqual(backtest.validate_data(result)['funding_coverage'],'missing_or_incomplete')
            self.assertEqual(len([c for c in calls if c[0].endswith('/klines')]),3)
            self.assertEqual(len(digest),64)
            self.assertTrue(Path(path).exists())

    def test_start_persists_running_and_init_marks_interruption_failed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(journal,'DB',Path(tmp)/'db.sqlite3'), patch.object(backtest,'RESEARCH',Path(tmp)):
            backtest.init()
            pending=backtest._new(dict(symbol='ETHUSDT'))
            self.assertEqual(backtest.get_run(pending['id'])['status'],'running')
            with self.assertRaisesRegex(ValueError,'进行中'):
                backtest.start(dict(symbol='ETHUSDT'))
            backtest.init()
            self.assertEqual(backtest.get_run(pending['id'])['status'],'failed')
            release=threading.Event()
            def source(config):
                release.wait(2)
                return breakout_data(),'fixture.json','hash'
            with patch.object(backtest,'load_data',side_effect=source):
                running=backtest.start(dict(symbol='ETHUSDT',strategy_id='breakout'))
                self.assertEqual(running['status'],'running')
                release.set()
                for _ in range(100):
                    done=backtest.get_run(running['id'])
                    if done['status']!='running': break
                    time.sleep(.01)
                self.assertEqual(done['status'],'completed')

    def test_stale_local_archive_does_not_pretend_to_be_latest(self):
        archived=dataset(2160)
        with tempfile.TemporaryDirectory() as tmp, patch.object(backtest,'RESEARCH',Path(tmp)):
            path=Path(tmp)/'ETHUSDT-1h-90d-input.json'
            path.write_text(json.dumps(archived))
            with patch.object(journal,'now_ms',return_value=archived['requested_end']+1+HOUR), patch.object(backtest,'_public_get',side_effect=ValueError('new network request')):
                with self.assertRaisesRegex(ValueError,'new network request'):
                    backtest.load_data(self.config())
            cfg=self.config(dataset_id='eth-1h-20261015')
            data,_,_=backtest.load_data(cfg)
            self.assertEqual(data['requested_end'],archived['requested_end'])
            with self.assertRaises(ValueError): self.config(dataset_id='/tmp/other-file.json')

    def test_failed_and_completed_experiments_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(journal,'DB',Path(tmp)/'db.sqlite3'), patch.object(backtest,'RESEARCH',Path(tmp)):
            backtest.init()
            bad=dataset(); del bad['bars'][10]
            with patch.object(backtest,'load_data',return_value=(bad,'fixture.json','bad-hash')):
                failed=backtest.run(dict(symbol='ETHUSDT',strategy_id='breakout'))
            self.assertEqual(failed['status'],'failed')
            self.assertIn('缺口',failed['error'])
            with patch.object(backtest,'load_data',return_value=(breakout_data(),'fixture.json','good-hash')):
                done=backtest.run(dict(symbol='ETHUSDT',strategy_id='breakout'))
            self.assertEqual(done['status'],'completed')
            self.assertEqual(len(backtest.list_runs()),2)
            self.assertEqual(backtest.get_run(failed['id'])['status'],'failed')
            self.assertTrue(Path(done['archive_path']).exists())
            self.assertEqual(done['summary']['evidence_status'],'insufficient')
            self.assertEqual(len(done['cost_sensitivity']),3)
            json.dumps(done,allow_nan=False)


if __name__=='__main__': unittest.main()
