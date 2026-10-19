import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import journal
import lessons
import trading_system as ts


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.p=patch.object(journal,'DB',Path(self.temp.name)/'j.sqlite3');self.p.start();journal.init()

    def tearDown(self):
        self.p.stop();self.temp.cleanup()

    def test_seeds_draft_once(self):
        a=ts.active();b=ts.active()
        self.assertEqual((a['version'],b['version']),(1,1))
        self.assertFalse(a['data']['rules']['confirmed'])
        self.assertEqual(a['data']['rules'],{'min_rr':1.5,'account_risk_pct':2,'trial_fraction':0.5,'confirmed':False})
        self.assertEqual([s['id'] for s in a['data']['setups']],['support_bounce','strength_follow','exhaustion_reversal','discretionary'])
        for s in a['data']['setups']:
            self.assertTrue(s['name'] and s['summary']);self.assertIn(s['status'],('active','paused'))
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM trading_system_versions').fetchone()[0],1)

    def test_confirm_creates_version(self):
        ts.active()
        v=ts.confirm({'account_risk_pct':1.5})
        self.assertEqual(v['version'],2);self.assertTrue(v['data']['rules']['confirmed'])
        self.assertEqual(v['data']['rules']['account_risk_pct'],1.5);self.assertEqual(v['data']['rules']['min_rr'],1.5)
        self.assertEqual(ts.active()['version'],2)
        self.assertEqual([h['status'] for h in ts.history()],['active','superseded'])

    def test_confirm_validates_range(self):
        for bad in ({'account_risk_pct':50},{'account_risk_pct':0},{'min_rr':0.5},{'min_rr':'2'},{'account_risk_pct':True}):
            with self.assertRaises(ValueError):ts.confirm(bad)
        self.assertEqual(ts.active()['version'],1)

    def test_lessons_pick(self):
        self.assertEqual(len(lessons.LESSONS),10)
        for card in lessons.LESSONS.values():
            self.assertTrue(card['title'] and card['body'] and card['do'])
        picked=lessons.pick(['cooldown','nope','cooldown','size_by_stop','add_recalc','keep_targets'])
        self.assertEqual([c['code'] for c in picked],['cooldown','size_by_stop','add_recalc'])


if __name__=='__main__':
    unittest.main()
