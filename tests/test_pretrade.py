import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import base64
import json
import journal
import pretrade_ai
import pretrade_evidence


class FakeExchange:
    offset = 0
    def align_time(self): return journal.now_ms()
    def get(self, path, params=None, signed=False):
        if path.endswith('premiumIndex'): return {'markPrice':'2000','time':journal.now_ms()-40000}
        if path.endswith('positionRisk'): return []
        return []
    def income(self, start, end):
        return [{'asset':'USDT','incomeType':'REALIZED_PNL','income':'20'},
                {'asset':'USDT','incomeType':'COMMISSION','income':'-2'},
                {'asset':'USDT','incomeType':'FUNDING_FEE','income':'-1'},
                {'asset':'USDT','incomeType':'TRANSFER','income':'500'}]


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ledger=patch.object(pretrade_evidence,'refresh_ledger',return_value={'complete':True,'captured_at':journal.now_ms()})
        self.ledger_mock=self.ledger.start()
    def tearDown(self):self.ledger.stop()
    def test_reference_timestamp_is_not_relabelled_fresh_and_transfers_not_profit(self):
        evidence=pretrade_evidence.collect({'symbol':'ETHUSDT'},FakeExchange())
        self.assertGreater(journal.now_ms()-evidence['quote']['captured_at'],39000)
        self.assertEqual(evidence['account']['daily_realized_pnl_usdt'],'17')
        self.assertEqual(evidence['positions']['rows'],[])
        self.assertTrue(evidence['orders']['complete'])

    def test_read_failure_is_not_zero_loss_or_no_orders(self):
        client=FakeExchange()
        def broken(*args,**kwargs): raise RuntimeError('sensitive detail')
        client.income=broken
        evidence=pretrade_evidence.collect({'symbol':'ETHUSDT'},client)
        self.assertNotIn('account',evidence)
        self.assertTrue(evidence['errors'])
        self.assertNotIn('sensitive detail',str(evidence))
        self.ledger_mock.assert_not_called()

    def test_unconverted_non_usdt_cost_blocks_account_total(self):
        client=FakeExchange()
        client.income=lambda *args:[{'asset':'BNB','incomeType':'COMMISSION','income':'-1'}]
        evidence=pretrade_evidence.collect({'symbol':'ETHUSDT'},client)
        self.assertNotIn('account',evidence)


class ChartTests(unittest.TestCase):
    def test_chart_mapping_never_treats_20m_or_legacy_response_as_1h_evidence(self):
        exact={'strategy_id':'support_reclaim','direction':'LONG','timeframe':'1h','baseline_match':'exact'}
        self.assertEqual(pretrade_ai.checked_hypothesis(exact)['strategy_id'],'support_reclaim')
        for change in ({'timeframe':'20m'},{'baseline_match':'different'}, {'direction':'UNKNOWN'}):
            self.assertEqual(pretrade_ai.checked_hypothesis(dict(exact,**change))['strategy_id'],'unsupported')
        legacy={'strategy_id':'support_reclaim','direction':'LONG'}
        guarded=pretrade_ai.checked_hypothesis(legacy)
        self.assertEqual(guarded['strategy_id'],'unsupported')
        self.assertEqual(guarded['candidate_strategy_id'],'support_reclaim')
        self.assertEqual(legacy['strategy_id'],'support_reclaim')  # preserve raw AI evidence

    def test_decode_reencode_image_and_reject_spoofed_upload(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder,patch.object(journal,'ROOT',Path(folder)):
            (Path(folder)/'static/assets').mkdir(parents=True)
            stream=io.BytesIO();Image.new('RGB',(20,10)).save(stream,format='PNG')
            url='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
            asset=pretrade_ai.save_image(url)
            self.assertTrue(asset.startswith('/assets/chart-'))
            self.assertTrue((Path(folder)/'static'/asset.lstrip('/')).is_file())
            with self.assertRaises(ValueError): pretrade_ai.save_image('data:image/png;base64,'+base64.b64encode(b'<script>x</script>').decode())
            with self.assertRaises(ValueError): pretrade_ai.save_image('data:image/svg+xml;base64,PHN2Zy8+')

    def test_ai_worker_retains_changed_plan_after_stale_response(self):
        import training
        lock=pretrade_ai.ai_review.LOCK
        self.assertTrue(lock.acquire(blocking=False))
        with patch.object(pretrade_ai,'model_json',return_value={'verdict':'support'}), \
             patch.object(pretrade_ai.pretrade_evidence,'collect',return_value={}), \
             patch.object(training,'complete_review',side_effect=training.Conflict('changed')) as complete:
            pretrade_ai.review_worker({'id':'p','review_token':'old'},{})
        self.assertEqual(complete.call_count,1)
        self.assertFalse(lock.locked())


class FreshLedgerTests(unittest.TestCase):
    def setUp(self):
        import training
        self.folder=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.folder.name)/'db.sqlite3');self.dbpatch.start()
        journal.init();training.init()
        self.stamp=journal.now_ms()
        with patch.object(training,'now_ms',return_value=self.stamp-1000):
            training.save_policy({'version':0,'confirmed':True,'max_trade_risk_usdt':'20','max_daily_loss_usdt':'50','max_entries_per_day':1,'cooldown_seconds':60})
        self.position={'symbol':'ETHUSDT','positionSide':'BOTH','positionAmt':'1','entryPrice':'100','markPrice':'100','unRealizedProfit':'0'}
        self.fill={'symbol':'ETHUSDT','id':123,'orderId':456,'time':self.stamp-100,'side':'BUY','positionSide':'BOTH','qty':'1','price':'100','commission':'0.05','commissionAsset':'USDT','realizedPnl':'0'}
        self.income=[{'symbol':'ETHUSDT','tranId':1,'time':self.stamp-100,'incomeType':'COMMISSION','asset':'USDT','income':'-0.05'}]
        self.client=FakeExchange()
        self.client.trades=lambda *args:[self.fill]
        self.client.income=lambda *args:self.income
        self.client.get=lambda *args,**kwargs:[self.position]
        self.start=training.day_bounds(self.stamp)[1]

    def tearDown(self):self.dbpatch.stop();self.folder.cleanup()

    def test_new_exchange_fill_is_audited_before_approval_counter(self):
        import training
        r=pretrade_evidence.refresh_ledger(self.client,{'positions':{'rows':[self.position]}},self.start,self.income)
        self.assertTrue(r['complete']);self.assertEqual(r['fill_count'],1)
        self.assertEqual(r['order_count'],1)
        self.assertEqual(training.today()['metrics']['new_risk_orders'],1)
        self.assertEqual(training.today()['metrics']['unreviewed_orders'],1)

    def test_changed_position_during_refresh_does_not_commit_false_complete_ledger(self):
        self.client.get=lambda *args,**kwargs:[dict(self.position,positionAmt='2')]
        with self.assertRaises(ValueError):
            pretrade_evidence.refresh_ledger(self.client,{'positions':{'rows':[self.position]}},self.start,self.income)
        with journal.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM trades').fetchone()[0],0)


if __name__=='__main__': unittest.main()
