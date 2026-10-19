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


import json as _json
import tempfile
from pathlib import Path
from unittest.mock import patch

import journal


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.p=patch.object(journal,'DB',Path(self.temp.name)/'j.sqlite3');self.p.start();journal.init()
        self.latest=patch('market_replay.latest',return_value=replay(UP));self.latest.start()

    def tearDown(self):
        self.latest.stop();self.p.stop();self.temp.cleanup()

    def put(self,jid,data,created=T0-60000):
        with journal.connect() as db:
            db.execute('INSERT INTO journals VALUES(?,?,?,?)',(jid,created,created,_json.dumps(data)))

    def check(self,cid,created,symbol='ETH',direction='LONG',state='no'):
        with journal.connect() as db:
            db.execute('INSERT INTO assistant_checks VALUES(?,?,?)',(cid,created,_json.dumps(dict(id=cid,created_at=created,
                plan=dict(symbol=symbol,direction=direction,stop_loss='98',take_profit='104'),judgment=dict(state=state)))))

    def test_get_caches_links_and_counts_recent_losses(self):
        r=record();r.pop('id');self.put('j1',r)
        loss=record(realized='-5',closed=T0-5*60000);loss.pop('id');self.put('j0',loss,created=T0-3600000)
        self.check('old',T0-3*3600000);self.check('other',T0-600000,symbol='BTC');self.check('mine',T0-600000)
        a=mr.get('j1')
        self.assertEqual(a['check_id'],'mine');self.assertEqual(a['link'],'auto')
        self.assertEqual(a['next_action']['code'],'ignored_verdict')
        self.assertEqual(next(c for c in a['execution'] if c['code']=='cooldown')['status'],'fail')
        with patch.object(mr,'assess',side_effect=AssertionError('should use cache')):
            self.assertEqual(mr.get('j1')['quadrant'],a['quadrant'])

    def test_explicit_link_wins_and_open_trade_not_reviewed(self):
        r=record(pretrade_check_id='old');r.pop('id');self.put('j1',r);self.check('old',T0-3*3600000,state='go');self.check('mine',T0-600000)
        a=mr.get('j1');self.assertEqual((a['check_id'],a['link']),('old','manual'))
        o=record(status='持仓中');o['execution']['closed_ms']=None;o.pop('id');self.put('j2',o)
        self.assertEqual(mr.get('j2')['status'],'not_closed')
        with self.assertRaises(ValueError):mr.get('missing')

    def test_adopt_writes_reminder_not_personal_review(self):
        r=record(realized='1');r.pop('id');self.put('j1',r)
        item=mr.adopt('j1')
        self.assertEqual(item['instruction'],mr.NEXT['early_exit'])
        with journal.connect() as db:
            data=_json.loads(db.execute("SELECT data FROM journals WHERE id='j1'").fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM review_actions').fetchone()[0],1)
        self.assertNotIn('review_next',data);self.assertNotIn('reflection',data)
        mr.adopt('j1')
        with journal.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM review_actions').fetchone()[0],1)
        clean=record();clean.pop('id');self.put('j3',clean)
        with self.assertRaises(ValueError):mr.adopt('j3')

    def test_recent_codes_counts_fails(self):
        for i in range(3):
            r=record(realized='1',closed=T0+(10+i)*STEP);r.pop('id');self.put('j%d'%i,r);mr.get('j%d'%i)
        self.assertEqual(mr.recent_codes()['early_exit'],3)
        self.assertEqual(mr.lesson_order(['size_by_stop','early_exit']),['early_exit','size_by_stop'])
        self.assertEqual(mr.lesson_order([]),['early_exit'])
