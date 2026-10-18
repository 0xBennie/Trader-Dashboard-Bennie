import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import journal
import review_learning
import review_automation as auto


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Hong_Kong')).timestamp()*1000)


class AutomaticReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(journal, 'DB', Path(self.temp.name)/'journal.sqlite3')
        self.dbpatch.start()
        journal.init()
        self.now = stamp('2026-10-18T21:05:00')

    def tearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def record(self, **extra):
        data = dict(symbol='DEMO', exchange='variational', status='持仓中',
                    entry_price='100', quantity='2', direction='LONG', thesis='原始判断')
        data.update(extra)
        with patch.object(journal, 'now_ms', return_value=self.now):
            j = journal.save_journal(data)
        with journal.connect() as db:
            d = json.loads(db.execute('SELECT data FROM journals WHERE id=?', (j['id'],)).fetchone()[0])
            d['screenshot_snapshot'] = {'source':'user_screenshot'}
            d.update(extra)
            db.execute('UPDATE journals SET data=? WHERE id=?', (json.dumps(d), j['id']))
        return j['id']

    def test_hong_kong_cutoff_and_no_overlap(self):
        self.assertEqual(auto.period(self.now)[1], stamp('2026-10-18T21:00:00'))
        self.assertEqual(auto.period(stamp('2026-10-18T20:59:59'))[1], stamp('2026-10-17T21:00:00'))
        start, end = auto.period(self.now)
        self.assertEqual(end-start, 86400000)

    def test_browser_close_enters_review_without_fabricated_net(self):
        jid = self.record(status='已平仓', browser_close=dict(source='authorized_browser',
            trade_status='confirmed', order_status='Completed', reduce_only=True,
            quantity='2', closed_ms=self.now-600000, realized_pnl_display='-3.25',
            realized_pnl_asset='USDC', fees=None, funding=None))
        with journal.connect() as db:
            records = journal.all_journals(db)
            progress = journal.review_progress(db, records, self.now)
            self.assertEqual(progress['pending'][0]['id'], jid)
            self.assertEqual(review_learning.summary(db, records, self.now)['closed_count'], 1)
            report = auto.build(db, *auto.period(self.now), self.now)
        self.assertEqual(report['closed'][0]['result']['realized'], '-3.25')
        self.assertIsNone(report['closed'][0]['result']['net'])
        self.assertEqual(report['closed'][0]['result']['asset'], 'USDC')
        self.assertFalse(progress['pending'][0]['personal_complete'])

    def test_screenshot_and_partial_close_never_count_as_completed(self):
        self.record(status='已平仓', screenshot_snapshot={'unrealized_pnl':'100'}, browser_close=dict(
            source='authorized_browser', trade_status='confirmed', order_status='Completed',
            reduce_only=True, quantity='1', closed_ms=self.now-1))
        with journal.connect() as db:
            report = auto.build(db, *auto.period(self.now), self.now)
        self.assertEqual(report['closed'], [])
        self.assertEqual(len(report['needs_verification']), 1)

    def test_daily_is_durable_idempotent_and_excludes_after_cutoff(self):
        self.record()
        first = auto.ensure_daily(self.now)
        second = auto.ensure_daily(self.now+1000)
        self.assertEqual(first, second)
        self.assertEqual(first['closed'], [])
        self.assertEqual(first['recorded'], [])
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM daily_reviews').fetchone()[0], 1)

    def test_queue_dedup_no_key_no_attempt_and_recovers_interruption(self):
        jid = self.record()
        with journal.connect() as db:
            self.assertEqual(auto.enqueue(db, self.now), 1)
            self.assertEqual(auto.enqueue(db, self.now), 0)
        with patch.object(auto.ai_review, 'status', return_value={'available':False}), patch.object(auto.ai_review, 'start') as start:
            auto.process_queue(self.now+60000)
            start.assert_not_called()
        with journal.connect() as db:
            row = db.execute('SELECT * FROM auto_reviews').fetchone()
            self.assertEqual(row['status'], 'pending')
            db.execute("UPDATE auto_reviews SET status='running',analysis_id='interrupted'")
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)', ('interrupted',jid,self.now,'failed','{}'))
        with patch.object(auto.ai_review, 'status', return_value={'available':False}):
            auto.process_queue(self.now+120000)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM auto_reviews').fetchone()[0], 'pending')

    def test_failure_retries_are_bounded_and_user_text_stays_original(self):
        jid = self.record()
        with journal.connect() as db:
            auto.enqueue(db, self.now)
        with patch.object(auto.ai_review, 'status', return_value={'available':True}), patch.object(auto.ai_review, 'start', side_effect=ValueError('bad connection')):
            for i in range(4):
                auto.process_queue(self.now+(i+1)*3600000)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM auto_reviews').fetchone()[0], 'failed')
            self.assertEqual(json.loads(db.execute('SELECT data FROM journals WHERE id=?',(jid,)).fetchone()[0])['thesis'], '原始判断')

    def test_worker_daily_runs_without_exchange_or_ai(self):
        worker = auto.Worker()
        with patch.object(auto.ai_review, 'status', return_value={'available':False}), patch.object(journal, 'now_ms', return_value=self.now):
            worker.tick()
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM daily_reviews').fetchone()[0], 1)
            self.assertEqual(auto.status(db, self.now)['ai_state'], 'not_configured')

    def test_successful_ai_job_completes_once(self):
        jid=self.record()
        with journal.connect() as db:auto.enqueue(db,self.now)
        def start(_):
            with journal.connect() as db:
                db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)',('done',jid,self.now+60000,'complete','{}'))
            return 'done'
        with patch.object(auto.ai_review,'status',return_value={'available':True}), patch.object(auto.ai_review,'start',side_effect=start) as generate:
            auto.process_queue(self.now+60000)
            auto.process_queue(self.now+120000)
            generate.assert_called_once_with(jid)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM auto_reviews').fetchone()[0],'complete')
            self.assertEqual(auto.enqueue(db,self.now+180000),0)

    def test_cutoff_excludes_start_and_future_closes(self):
        start,end=auto.period(self.now)
        for value in (start,end,end+1):
            self.record(status='已平仓',execution=dict(closed_ms=value,history_complete=True,realized_pnl='1',fees={'USDT':'0.1'}))
        with journal.connect() as db:
            report=auto.build(db,start,end,self.now)
        self.assertEqual(len(report['closed']),1)
        self.assertEqual(report['closed'][0]['closed_at'],end)


if __name__ == '__main__':
    unittest.main()
