import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import journal
import browser_journal
import review_learning


class BrowserJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.temp.name)/'db.sqlite3');self.dbpatch.start()
        journal.init();self.now=journal.now_ms()
        self.row=dict(identity='demo:fill-1',symbol='DEMO',direction='LONG',entry_price='100',quantity='2',
                      position={'unrealized_pnl':'-1'},orders=None)
    def tearDown(self):
        self.dbpatch.stop();self.temp.cleanup()
    def payload(self,**extra):
        return dict(dict(observed_at=self.now,source_url='https://omni.variational.io/perpetual/DEMO',status='complete',records=[self.row]),**extra)
    def test_replay_and_missing_position_do_not_duplicate_or_close(self):
        first=browser_journal.ingest(self.payload())
        second=browser_journal.ingest(self.payload())
        self.assertEqual(first['records'][0]['id'],second['records'][0]['id'])
        browser_journal.ingest(self.payload(observed_at=self.now+1,records=[]))
        with journal.connect() as db:
            rows=journal.all_journals(db)
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'持仓中')
            self.assertNotIn('execution',rows[0]);self.assertEqual(rows[0]['thesis'],'')
            self.assertIsNone(rows[0]['browser_observation']['orders'])
            self.assertEqual(db.execute('SELECT count(*) FROM browser_observations').fetchone()[0],1)
    def test_verified_close_preserves_user_notes_and_enters_queue(self):
        jid=browser_journal.ingest(self.payload())['records'][0]['id']
        journal.save_journal({'id':jid,'thesis':'我的原话','stop_loss':'95'})
        self.row['close']=dict(closed_ms=self.now,quantity='2',close_price='99',trade_display_id='close-1',
            trade_status='confirmed',order_status='Completed',reduce_only=True,realized_pnl_display='-2',realized_pnl_asset='USDC')
        browser_journal.ingest(self.payload(observed_at=self.now+1))
        with journal.connect() as db:
            row=journal.all_journals(db)[0]
            self.assertEqual(row['status'],'已平仓');self.assertEqual(row['thesis'],'我的原话');self.assertEqual(row['stop_loss'],'95')
            self.assertEqual(review_learning.closed(row),self.now);self.assertIsNone(row['browser_close']['net_pnl'])
    def test_login_failure_preserves_last_good_observation(self):
        browser_journal.ingest(self.payload())
        browser_journal.ingest(self.payload(observed_at=self.now+1,status='login_required',records=[]))
        with journal.connect() as db:
            s=journal.kv('variational_browser_status',{},db)
            self.assertEqual(s['last_success'],self.now);self.assertEqual(s['status'],'login_required')
            self.assertEqual(journal.all_journals(db)[0]['status'],'持仓中')
    def test_invalid_partial_close_and_source_are_rejected(self):
        for change in ({'source_url':'https://evil.example/'},{'observed_at':self.now+120000}):
            with self.assertRaises(ValueError):browser_journal.ingest(self.payload(**change))
        self.row['close']=dict(closed_ms=self.now,quantity='1',close_price='99',trade_display_id='close-1',
            trade_status='confirmed',order_status='Completed',reduce_only=True)
        with self.assertRaises(ValueError):browser_journal.ingest(self.payload())
        with journal.connect() as db:self.assertEqual(journal.all_journals(db),[])

if __name__=='__main__':unittest.main()
