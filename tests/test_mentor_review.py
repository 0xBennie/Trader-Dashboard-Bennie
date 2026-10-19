import unittest

import mentor_review as mr

T0=1_800_000_000_000
STEP=300000


def bars(path,start=T0):
    """path: list of (high, low); open/close are midpoints."""
    out=[]
    for i,(h,l) in enumerate(path):
        t=start+i*STEP;m=(h+l)/2
        out.append(dict(time=t,close_time=t+STEP-1,open=str(m),high=str(h),low=str(l),close=str(m),volume='1'))
    return out


def replay(path,kind='entry'):
    return dict(anchor_kind=kind,source=dict(exchange='binance',reference_only=False),
                windows=[dict(kind='entry' if kind=='entry' else 'observation',interval='5m',anchor=T0,candles=bars(path))])


def record(direction='LONG',realized='4',events=None,closed=T0+10*STEP,**extra):
    r=dict(id='j1',symbol='ETHUSDT',exchange='binance',direction=direction,status='已平仓',entry_price='100',quantity='1',
           stop_loss='98' if direction=='LONG' else '102',take_profit='104' if direction=='LONG' else '96',
           execution=dict(opened_ms=T0,closed_ms=closed,realized_pnl=realized,exit_quantity='1',entry_quantity='1',
                          history_complete=True,fees={'USDT':'0'},events=events or [dict(kind='open',order_key='a'),dict(kind='close',order_key='z')]))
    r.update(extra);return r


CTX=dict(losses=[],cooldown_minutes=30,cap_amount=200.0)
UP=[(100.5,99.5),(102,100),(104.5,101)]           # target first
DOWN=[(100.5,99.5),(99.5,97.5),(105,99)]          # stop first


class AssessTests(unittest.TestCase):
    def test_target_first_clean_execution(self):
        a=mr.assess(record(),replay(UP),None,CTX)
        self.assertEqual(a['status'],'complete')
        self.assertEqual(a['judgment']['state'],'right');self.assertEqual(a['judgment']['first_hit'],'target')
        self.assertEqual(a['quadrant'],'right_clean');self.assertIsNone(a['next_action'])
        self.assertAlmostEqual(a['exit_price'],104.0)

    def test_stop_first_and_exit_at_stop_is_good_loss(self):
        a=mr.assess(record(realized='-2'),replay(DOWN),None,CTX)
        self.assertEqual(a['judgment']['first_hit'],'stop');self.assertEqual(a['quadrant'],'good_loss')
        self.assertIn('好的亏损',a['quadrant_label']);self.assertIsNone(a['next_action'])
        self.assertAlmostEqual(a['judgment']['mae_r'],1.25)

    def test_profit_inside_cooldown_is_lucky(self):
        ctx=dict(CTX,losses=[T0-10*60000])
        a=mr.assess(record(),replay(UP),None,ctx)
        self.assertEqual(a['quadrant'],'lucky');self.assertEqual(a['next_action']['code'],'cooldown')
        self.assertIn('cooldown',a['lessons'])

    def test_unknown_entry_time_stays_unknown(self):
        r=record();r['execution']['opened_ms']=None
        a=mr.assess(r,replay(UP,kind='recorded_observation'),None,CTX)
        self.assertEqual(a['judgment']['state'],'unknown');self.assertIsNone(a['judgment']['mfe_r'])
        self.assertIn('入场时间未知',a['judgment']['note'])

    def test_ignored_verdict_has_top_priority(self):
        check=dict(id='c1',judgment=dict(state='no'),created_at=T0-60000)
        a=mr.assess(record(realized='0.5'),replay(UP),check,dict(CTX,losses=[T0-60000]))
        codes=[c['code'] for c in a['execution'] if c['status']=='fail']
        self.assertIn('ignored_verdict',codes);self.assertEqual(a['next_action']['code'],'ignored_verdict')
        self.assertEqual(a['check_state'],'no')

    def test_add_detection_binance_and_unknown_for_variational(self):
        ev=[dict(kind='open',order_key='a'),dict(kind='open',order_key='a'),dict(kind='add',order_key='b'),dict(kind='close',order_key='z')]
        a=mr.assess(record(events=ev),replay(UP),None,CTX)
        self.assertEqual(next(c for c in a['execution'] if c['code']=='add_recalc')['status'],'fail')
        v=record(exchange='variational',execution=None,opened_at='2027-01-15T16:00:00+08:00',
                 browser_close=dict(source='authorized_browser',trade_status='confirmed',order_status='Completed',reduce_only=True,
                                    quantity='1',closed_ms=T0+10*STEP,close_price='104',realized_pnl_display='4',realized_pnl_asset='USDC'))
        v['opened_at']=None
        a=mr.assess(v,replay(UP,kind='recorded_observation'),None,CTX)
        self.assertEqual(next(c for c in a['execution'] if c['code']=='add_recalc')['status'],'unknown')
        self.assertAlmostEqual(a['exit_price'],104.0);self.assertTrue(a['reference_only'])

    def test_early_exit_and_short_mirror(self):
        a=mr.assess(record(realized='1'),replay(UP),None,CTX)  # exit 101: neither stop nor target
        self.assertEqual(next(c for c in a['execution'] if c['code']=='early_exit')['status'],'fail')
        self.assertEqual(a['quadrant'],'lucky');self.assertEqual(a['next_action']['code'],'early_exit')
        down=[(100.5,99.5),(99,98),(99,95.5)]
        s=mr.assess(record(direction='SHORT'),replay(down),None,CTX)
        self.assertEqual(s['judgment']['first_hit'],'target');self.assertEqual(s['quadrant'],'right_clean')
        self.assertAlmostEqual(s['exit_price'],96.0)

    def test_missing_stop(self):
        a=mr.assess(record(stop_loss=''),replay(UP),None,CTX)
        self.assertEqual(next(c for c in a['execution'] if c['code']=='no_stop')['status'],'fail')
        self.assertEqual(a['judgment']['state'],'unknown');self.assertEqual(a['next_action']['code'],'no_stop')

    def test_open_record_not_assessed(self):
        r=record(status='持仓中');r['execution']['closed_ms']=None
        self.assertEqual(mr.assess(r,replay(UP),None,CTX)['status'],'not_closed')


if __name__=='__main__':
    unittest.main()
