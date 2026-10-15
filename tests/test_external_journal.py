import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import ai_review
import journal
from fixtures import create_journal
import recorder
import server


class ExternalJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = journal.DB
        journal.DB = Path(self.temp.name) / 'test.sqlite3'
        journal.init()
        create_journal()

    def tearDown(self):
        journal.DB = self.old
        self.temp.cleanup()

    def create(self, **extra):
        return journal.save_journal(dict(exchange='variational', symbol='DEMO', direction='LONG',
            entry_price='100.25', quantity='2.4', stop_loss='95.25', take_profit='110.25',
            status='持仓中', timeframe='30m', **extra))

    def test_edit_keeps_exchange_currency_and_missing_open_time(self):
        saved = self.create()
        updated = journal.save_journal({'id': saved['id'], 'reflection': '继续观察'})
        self.assertEqual(updated['exchange'], 'variational')
        self.assertEqual(updated['quote_currency'], 'USD')
        self.assertEqual(updated['market'], 'Variational 永续')
        self.assertEqual(updated['opened_at'], '')
        self.assertEqual(updated['thesis'], '')
        self.assertEqual(journal.risk_math(updated)['risk'], '12.000')
        with self.assertRaises(ValueError):
            journal.save_journal({'id': saved['id'], 'exchange': 'binance', 'symbol': 'DEMOUSDT'})
        with journal.connect() as db:
            self.assertNotIn(saved['id'], [j['id'] for j in journal.binance_journals(db)])
            self.assertIn('fixture-eth', [j['id'] for j in journal.binance_journals(db)])

    def test_sync_and_recorder_never_request_external_contract(self):
        self.create()
        client = Mock(offset=0)
        client.align_time.return_value = journal.now_ms()
        client.get.return_value = []
        client.income.return_value = []
        client.trades.return_value = []
        with patch.object(server, 'Binance', return_value=client), patch.object(server, 'credentials', return_value=('test', 'test')), patch.object(server.trade_context, 'collect', return_value={}) as collect:
            server.sync_account(7)
            self.assertNotIn('DEMO', collect.call_args.args[1])
            self.assertTrue(all(c.args[0] != 'DEMO' for c in client.trades.call_args_list))
        r = recorder.Recorder(threading.Lock())
        r.client = client
        with patch.object(recorder.trade_context, 'collect', return_value={}):
            r.poll()
        self.assertTrue(all(c.args[0] != 'DEMO' for c in client.trades.call_args_list))

    def test_same_symbol_cannot_borrow_binance_positions_or_fills(self):
        saved = self.create()
        with journal.connect() as db:
            journal.set_kv(db, 'snapshot', {'positions': [{'symbol':'DEMO','positionAmt':'999'}], 'captured_at':journal.now_ms()})
            journal.set_kv(db, 'trade_context', {'DEMO': {'status':'complete','orders':['wrong exchange']}})
        _, evidence = ai_review.evidence(saved['id'])
        self.assertEqual(evidence['positions'], [])
        self.assertEqual(evidence['exchange_orders_and_planning'], {})
        self.assertEqual(evidence['sync']['status'], 'not_connected')
        self.assertEqual(evidence['computed']['quote_currency'], 'USD')

    def test_ai_uses_saved_attachments_without_entry_prefix_and_rejects_escape(self):
        root = Path(self.temp.name)
        assets = root / 'static/assets'
        assets.mkdir(parents=True)
        image = assets / 'variational-example.png'
        image.write_bytes(b'test')
        (root/'outside.png').write_bytes(b'test')
        (assets/'escape.png').symlink_to(root/'outside.png')
        with patch.object(journal, 'ROOT', root):
            actual = ai_review.image_paths({'image':'/assets/variational-example.png', 'images':[
                {'url':'/assets/variational-example.png'}, {'url':'/assets/../../outside.png'},
                {'url':'https://example.com/outside.png'}, {'url':'/assets/escape.png'}]})
        self.assertEqual(actual, [image.resolve()])


if __name__ == '__main__':
    unittest.main()
