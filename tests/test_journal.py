import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
import journal
from fixtures import create_journal
import server
from binance_client import Binance, BinanceError


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = journal.DB
        journal.DB = Path(self.temp.name)/"test.sqlite3"
        journal.init()

    def tearDown(self):
        journal.DB = self.old_db
        self.temp.cleanup()

    def test_fresh_install_starts_empty_and_reinitialization_is_safe(self):
        journal.init()
        state = journal.state()
        self.assertEqual(state["trades"], [])
        self.assertEqual(state["journals"], [])
        self.assertEqual(state["analyses"], [])

    def test_journal_persistence_and_revision(self):
        saved = journal.save_journal({"symbol":"ETHUSDT", "title":"实际理由", "direction":"LONG", "entry_price":"100.123456789", "quantity":"2"})
        journal.save_journal({"id":saved["id"], "title":"修改后的标题"})
        state = journal.state()
        record = next(j for j in state["journals"] if j["id"] == saved["id"])
        self.assertEqual(record["entry_price"], "100.123456789")
        self.assertEqual(record["title"], "修改后的标题")
        with journal.connect() as db:
            old = json.loads(db.execute("SELECT data FROM revisions WHERE journal_id=?", (saved["id"],)).fetchone()[0])
        self.assertEqual(old["title"], "实际理由")

    def test_nonfinite_negative_invalid_symbol_rejected(self):
        for value in ("NaN", "Infinity", "-3", "0", "invalid"):
            with self.assertRaises(ValueError):
                journal.save_journal({"entry_price": value})
        with self.assertRaises(ValueError):
            journal.save_journal({"symbol":"<script>"})

    def test_long_short_risk_and_bad_stop(self):
        for side, stop, target in [("LONG","90","120"),("SHORT","110","80")]:
            result=journal.risk_math({"direction":side,"entry_price":"100","stop_loss":stop,"take_profit":target,"quantity":"2"})
            self.assertEqual(Decimal(result["risk"]), Decimal(20))
            self.assertEqual(Decimal(result["rr"]), Decimal(2))
        result=journal.risk_math({"direction":"LONG","entry_price":"100","stop_loss":"110","take_profit":"120","quantity":"2"})
        self.assertFalse(result["valid"])

    def test_net_pnl_uses_signed_ledger_excludes_transfers_separates_assets(self):
        rows=[{"asset":a,"incomeType":t,"income":v,"time":i} for i,(a,t,v) in enumerate([
            ("USDT","REALIZED_PNL","10.1"),("USDT","COMMISSION","-0.2"),("USDT","FUNDING_FEE","-0.1"),
            ("USDT","TRANSFER","10000"),("BNB","COMMISSION","-0.01")])]
        totals, curve=journal.aggregate_income(rows)
        self.assertEqual(totals["USDT"]["net"],"9.8")
        self.assertEqual(totals["BNB"]["net"],"-0.01")
        self.assertEqual(curve[-1]["value"],"9.8")

    def test_readonly_endpoint_allowlist(self):
        with self.assertRaises(BinanceError):
            Binance().get("/fapi/v1/order", {"side":"BUY"}, True)

    def test_full_page_splits_without_losing_same_time_trades(self):
        client=Binance()
        data=[{"id":i,"time":1000 if i<800 else 1001} for i in range(1200)]
        def fake_get(path, params, signed):
            return [r for r in data if params["startTime"]<=r["time"]<=params["endTime"]][-1000:]
        with patch.object(client,"get",side_effect=fake_get):
            rows=client.trade_window("ETHUSDT",1000,1001)
        self.assertEqual(len(rows),1200)
        self.assertEqual(len({r['id'] for r in rows}),1200)

    def test_sync_idempotence_and_failure_preserves_snapshot(self):
        class Fake:
            def __init__(self,*args):pass
            def align_time(self):return 2000000000000
            def get(self,*args,**kwargs):return [{"symbol":"ETHUSDT","positionAmt":"1"}]
            def income(self,*args):return [{"symbol":"ETHUSDT","asset":"USDT","incomeType":"COMMISSION","tranId":1,"income":"-1","time":1999999999999}]
            def trades(self,*args):return [{"symbol":"ETHUSDT","id":1,"time":1999999999999}]
        with patch.object(server,"Binance",Fake):
            server.sync_account(30);server.sync_account(30)
        state=journal.state()
        self.assertEqual(len(state["trades"]),1)
        self.assertEqual(state["totals"]["USDT"]["fees"],"-1")
        snapshot=state["snapshot"]
        with patch.object(server,"Binance",side_effect=BinanceError("test failure")):
            server.sync_account(30)
        state=journal.state()
        self.assertEqual(state["sync"]["status"],"failed")
        self.assertEqual(state["snapshot"],snapshot)
        self.assertEqual(len(state["trades"]),1)

    def test_ai_payload_has_no_credentials_or_order_ids(self):
        create_journal()
        import ai_review
        _, evidence=ai_review.evidence("fixture-eth")
        serialized=json.dumps(evidence)
        for marker in ("BINANCE_API", "api_key", "secret", "csrf", "orderId"):
            self.assertNotIn(marker,serialized)

    def test_order_grouping_keeps_exact_ids_and_separate_fee_assets(self):
        base={"symbol":"ETHUSDT","orderId":9007199254740993001,"side":"BUY","positionSide":"LONG","realizedPnl":"0","time":1}
        fills=[dict(base,id=1,qty="2",price="100",commission="0.1",commissionAsset="USDT"),
               dict(base,id=2,qty="1",price="103",commission="0.001",commissionAsset="BNB"),
               dict(base,id=3,orderId=9007199254740993002,qty="1",price="105",commission="0.1",commissionAsset="USDT")]
        groups=journal.group_orders(fills)
        self.assertEqual(len(groups),2)
        group=next(g for g in groups if g['order_key']==str(base['orderId']))
        self.assertEqual(group['price'],'101')
        self.assertEqual(group['fill_count'],2)
        self.assertEqual(group['fees'],{'USDT':'0.1','BNB':'0.001'})

    def test_actual_order_protection_and_net_break_even(self):
        from trade_context import planning,normalize_orders
        position={'entryPrice':'100','positionAmt':'2','markPrice':'102','positionSide':'LONG','breakEvenPrice':'100.05'}
        orders=normalize_orders([{'symbol':'ETHUSDT','orderType':'STOP_MARKET','side':'SELL','positionSide':'LONG','algoStatus':'NEW','triggerPrice':'95','quantity':'0','closePosition':True},
                                 {'symbol':'ETHUSDT','orderType':'TAKE_PROFIT_MARKET','side':'SELL','positionSide':'SHORT','algoStatus':'NEW','triggerPrice':'110','quantity':'0','closePosition':True}])
        result=planning(position,orders,'0.0005','0.01')
        self.assertEqual(result['stop_loss'],'95')
        self.assertNotIn('take_profit',result)
        self.assertEqual(result['estimated_net_break_even'],'100.11')
        self.assertEqual(result['gross_stop_loss'],'10')
        self.assertEqual(Decimal(result['price_change_pct']),Decimal(2))

    def test_partial_stop_does_not_claim_full_coverage(self):
        from trade_context import planning,normalize_orders
        position={'entryPrice':'100','positionAmt':'2','markPrice':'102','positionSide':'LONG','breakEvenPrice':'100.05'}
        orders=normalize_orders([{'symbol':'ETHUSDT','type':'STOP_MARKET','side':'SELL','positionSide':'LONG','status':'NEW','stopPrice':'95','origQty':'1','closePosition':False}])
        result=planning(position,orders,None)
        self.assertNotIn('stop_loss',result)
        self.assertNotIn('estimated_net_break_even',result)

    def test_custom_profit_protection_plan_persists_without_default(self):
        create_journal()
        seed=journal.state()['journals'][0]
        self.assertEqual(seed['protect_trigger_pct'], '')
        journal.save_journal({'id':seed['id'],'protect_trigger_pct':'1.7','protect_price':'101.25'})
        stored=journal.state()['journals'][0]
        self.assertEqual(stored['protect_trigger_pct'],'1.7')
        self.assertEqual(stored['protect_price'],'101.25')

    def test_field_conflict_preserves_both_history_and_latest(self):
        create_journal()
        journal.save_journal({'id':'fixture-eth','thesis':'newer','stop_loss':'95'})
        with self.assertRaises(journal.JournalConflict):
            journal.save_journal({'id':'fixture-eth','thesis':'stale overwrite','base_values':{'thesis':''}})
        saved=journal.save_journal({'id':'fixture-eth','emotion':'平静','base_values':{'emotion':'未记录'}})
        self.assertEqual(saved['thesis'],'newer');self.assertEqual(saved['stop_loss'],'95')

    def test_duplicate_save_does_not_create_duplicate_revision(self):
        create_journal()
        payload={'id':'fixture-eth','thesis':'kept'}
        journal.save_journal(payload);journal.save_journal(payload)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],1)


if __name__=='__main__':unittest.main()
