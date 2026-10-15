"""Synthetic exchange evidence only. Tests never request a real account."""
import base64
import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import ai_review
import journal
import recorder
import exchange_sync as sync
import stop_coverage
import training
from exchange_clients import Client, ExchangeError, DAY, windows

CREDENTIALS = {"key": "fixture-key", "secret": "fixture-secret", "passphrase": "fixture-phrase"}
INSTRUMENT = {"instId": "XYZ-USDT-SWAP", "ctVal": "0.1", "ctMult": "1", "ctValCcy": "XYZ", "ctType": "linear", "settleCcy": "USDT"}


def client(exchange):
    c = Client(exchange, CREDENTIALS)
    c.instruments = {"XYZ-USDT-SWAP": INSTRUMENT} if exchange == "okx" else {"XYZUSDT": {"symbol": "XYZUSDT", "contractType": "LinearPerpetual", "settleCoin": "USDT"}}
    return c


def fill(ident="1", buy=True, stamp=1000, pnl="0"):
    return dict(id=ident, orderId=ident, symbol="XYZUSDT", positionSide="BOTH", side="BUY" if buy else "SELL", qty="2", price="100" if buy else "110", time=stamp, commission="0.1", commissionAsset="USDT", realizedPnl=pnl)


def position():
    return dict(symbol="XYZUSDT", positionSide="BOTH", positionAmt="2", entryPrice="100", markPrice="102", unRealizedProfit="4", updateTime=1000)


def bundle(fills=None, positions=None):
    return dict(positions=[position()] if positions is None else positions, trades=fills if fills is not None else [fill()], income=[], orders=[], wallet={"equity":"1000","asset":"USDT"}, instruments={}, raw={})


class ClientTests(unittest.TestCase):
    def test_signatures_and_public_requests(self):
        c = client("okx")
        headers = c.headers("/api/v5/account/positions", "instType=SWAP", 1000)
        message = "1970-01-01T00:00:01.000ZGET/api/v5/account/positions?instType=SWAP"
        expected = base64.b64encode(hmac.new(b"fixture-secret", message.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(headers["OK-ACCESS-SIGN"], expected)
        self.assertEqual(headers["OK-ACCESS-PASSPHRASE"], "fixture-phrase")
        c = client("bybit")
        query = "category=linear&cursor=a%26b"
        expected = hmac.new(b"fixture-secret", ("1000fixture-key5000"+query).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(c.headers("/v5/position/list", query, 1000)["X-BAPI-SIGN"], expected)
        self.assertNotIn("X-BAPI-API-KEY", c.headers("/v5/market/time", "", 1000))
        with self.assertRaises(ExchangeError): c.get("/v5/order/create")

    def test_okx_contract_units_and_signed_positions(self):
        c = client("okx")
        row = dict(instId="XYZ-USDT-SWAP", pos="20", posSide="short", avgPx="100", markPx="102", upl="-4", liqPx="200", uTime="1000")
        self.assertEqual(c.positions([row])[0]["positionAmt"], "-2.0")
        row["posSide"]="net";row["pos"]="-20"
        self.assertEqual(c.positions([row])[0]["positionAmt"], "-2.0")
        c.instruments["XYZ-USDT-SWAP"] = dict(INSTRUMENT, ctValCcy="USD")
        with self.assertRaises(ExchangeError):c.quantity("XYZ-USDT-SWAP", "1")

    def test_usdt_perpetual_scope_and_missing_metadata(self):
        for venue in ("okx", "bybit"):
            c=client(venue)
            self.assertFalse(c.supported("XYZUSDT-26DEC30"))
            self.assertFalse(c.supported("XYZ-USDC-SWAP"))
            with self.assertRaises(ExchangeError):c.supported("NEW-USDT-SWAP" if venue=="okx" else "NEWUSDT")

    def test_okx_fill_fees_rebates_and_funding(self):
        c=client("okx")
        f=dict(instId="XYZ-USDT-SWAP",tradeId="1",ordId="20",fillTime="1000",ts="1050",fillPx="100",fillSz="20",fillPnl="5",fee="-0.2",feeCcy="USDT",posSide="net",side="sell")
        normalized=c.fills([f])[0]
        self.assertEqual((normalized["qty"],normalized["commission"],normalized["time"]),("2.0","0.2",1000))
        b=dict(instId=f["instId"],ccy="USDT",ts="1000",billId="1",type="2",pnl="5",fee="-0.2")
        rows=c.income([b,dict(b,billId="2",type="8",subType="173",pnl="-0.4"),dict(b,billId="3",type="1",pnl="1000")])
        totals,_=journal.aggregate_income(rows)
        self.assertEqual(totals["USDT"]["net"],"4.4")
        f["fee"]="0.1";self.assertEqual(c.fills([f])[0]["commission"],"-0.1")

    def test_bybit_join_hedge_gross_fees_funding_and_duplicate_bills(self):
        c=client("bybit")
        f=dict(symbol="XYZUSDT",execType="Trade",execId="exec1",orderId="order1",execTime="1000",execQty="2",execPrice="110",execFee="0.2",side="Buy",seq="100")
        b=dict(id="bill1",type="TRADE",symbol="XYZUSDT",tradeId="exec1",currency="USDT",cashFlow="20",fee="0.2",funding="0",transactionTime="1000")
        order=dict(orderId="order1",positionIdx=2)
        result=c.fills([f],[b,b],[order])[0]
        self.assertEqual(result["positionSide"],"SHORT")
        self.assertEqual(result["realizedPnl"],"20")
        self.assertEqual(result["commission"],"0.2")
        self.assertEqual(result["sequence"],"100")
        for bills,orders in (([],[order]),([b],[])):
            with self.assertRaises(ExchangeError):c.fills([f],bills,orders)
        income=c.income([b,dict(b,id="fund",type="SETTLEMENT",funding="-0.5"),dict(b,id="transfer",type="TRANSFER_IN",cashFlow="1000")])
        self.assertEqual(journal.aggregate_income(income)[0]["USDT"]["net"],"19.3")
        with self.assertRaises(ExchangeError):c.income([dict(b,currency="USDC")])

    def test_bybit_cursor_pagination_rejects_loop(self):
        c=client("bybit")
        with patch.object(c,"get",side_effect=[{"list":[{"id":"a"}],"nextPageCursor":"one"},{"list":[{"id":"b"}],"nextPageCursor":""}]) as get,patch("exchange_clients.time.sleep"):
            self.assertEqual(len(c.pages("/v5/execution/list",{})),2)
            self.assertEqual(get.call_args.args[1]["cursor"],"one")
        with patch.object(c,"get",return_value={"list":[{"id":"a"}],"nextPageCursor":"same"}),patch("exchange_clients.time.sleep"):
            with self.assertRaises(ExchangeError):c.pages("/v5/execution/list",{})

    def test_okx_pagination_uses_bill_id_without_losing_same_ms_fills(self):
        c=client("okx")
        with patch.object(c,"get",side_effect=[[{"billId":"20","ts":"1000"},{"billId":"19","ts":"1000"}],[{"billId":"18","ts":"1000"}]]) as get,patch("exchange_clients.time.sleep"):
            self.assertEqual(len(c.pages("/api/v5/trade/fills-history",{},limit=2)),3)
            self.assertEqual(get.call_args.args[1]["after"],"19")
        spans=list(windows(0,20*DAY))
        self.assertTrue(all(end-start<7*DAY for start,end in spans))
        self.assertTrue(all(a[1]+1==b[0] for a,b in zip(spans,spans[1:])))

    def test_stop_orders_oco_remaining_size_and_unknown_triggers(self):
        c=client("okx")
        o=dict(instId="XYZ-USDT-SWAP",posSide="net",side="sell",state="live",algoId="1",ordType="oco",slTriggerPx="90",slOrdPx="-1",tpTriggerPx="120",tpOrdPx="-1",sz="20",reduceOnly="true")
        rows=c.orders([o])
        self.assertEqual([x["type"] for x in rows],["STOP_MARKET","TAKE_PROFIT_MARKET"])
        self.assertEqual(rows[0]["quantity"],"2.0")
        c=client("bybit")
        o=dict(symbol="XYZUSDT",positionIdx=0,orderId="2",side="Sell",orderStatus="Untriggered",qty="2",leavesQty="1",stopOrderType="StopLoss",orderType="Market",triggerPrice="90",reduceOnly=True,triggerBy="LastPrice")
        rows=c.orders([o]);self.assertEqual(rows[0]["quantity"],"1")
        now=journal.now_ms()
        s=dict(snapshot={"captured_at":now,"positions":[position()]},trade_context={"XYZUSDT":{"status":"complete","captured_at":now,"positions":[position()],"orders":rows}})
        self.assertEqual(stop_coverage.summarize(s,now)["rows"][0]["status"],"partial")
        rows[0]["coverage_unknown"]=True
        self.assertEqual(stop_coverage.summarize(s,now)["rows"][0]["status"],"unknown")
        o.update(qty="0",leavesQty="0",closeOnTrigger=True)
        self.assertTrue(c.orders([o])[0]["closePosition"])

    def test_error_messages_never_echo_api_response_or_network_exception(self):
        c=client("bybit");response=Mock();response.read.return_value=b'{"retCode": 10003, "retMsg":"fixture-secret"}'
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        c.opener.open=Mock(return_value=response)
        with self.assertRaises(ExchangeError) as error:c.get("/v5/user/query-api")
        self.assertNotIn("fixture-secret",str(error.exception))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=journal.DB
        journal.DB=Path(self.temp.name)/"journal.sqlite3";journal.init()
    def tearDown(self):
        journal.DB=self.old;self.temp.cleanup()
    def commit(self,exchange,data=None):
        sync.commit(exchange,data or bundle(),0,5000,4000)

    def test_exchange_id_collisions_and_restart_idempotence(self):
        with journal.connect() as db:
            f=fill();db.execute("INSERT INTO trades VALUES(?,?,?,?)",(f["symbol"],f["id"],f["time"],json.dumps(f)))
            recorder.reconcile(db,[position()],4000)
        for venue in ("okx","bybit"):
            self.commit(venue);self.commit(venue)
        records=journal.state()["journals"]
        self.assertEqual(len(records),3)
        self.assertEqual(len({j["id"] for j in records}),3)
        with journal.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM trade_events WHERE kind='open'").fetchone()[0],3)
        s=journal.state();self.assertEqual(len(s["trades"]),1)
        self.assertEqual(len(s["exchanges"]["okx"]["trades"]),1)
        for j in records:
            _,evidence=ai_review.evidence(j["id"])
            self.assertEqual(evidence["fill_count"],1)
            self.assertIn(j["exchange"],evidence["source_scope"])

    def test_close_does_not_close_other_venue_and_preserves_notes(self):
        for x in ("okx","bybit"):self.commit(x)
        j=next(j for j in journal.state()["journals"] if j["exchange"]=="okx")
        journal.save_journal(dict(id=j["id"],thesis="我的原始理由"))
        self.commit("okx",bundle([fill(),fill("2",False,2000,"20")],[]))
        records=journal.state()["journals"]
        closed=next(j for j in records if j["exchange"]=="okx")
        self.assertEqual(closed["status"],"已平仓");self.assertEqual(closed["thesis"],"我的原始理由")
        self.assertEqual(closed["execution"]["realized_pnl"],"20")
        self.assertEqual(next(j for j in records if j["exchange"]=="bybit")["status"],"持仓中")
        with journal.connect() as db:self.assertFalse(training._risk_orders(db))

    def test_failure_preserves_snapshot_and_other_exchange_runs_independently(self):
        self.commit("okx")
        sync.write_config("okx",dict(CREDENTIALS,identity="id"))
        with patch.object(sync,"Client") as constructor:
            constructor.return_value.identity.return_value="id"
            constructor.return_value.offset=0
            constructor.return_value.fetch.side_effect=ExchangeError("读取失败")
            self.assertFalse(sync.collect("okx"))
        s=journal.state()["exchanges"]["okx"]
        self.assertEqual(s["snapshot"]["captured_at"],4000)
        self.assertEqual(s["sync"]["status"],"retrying")
        self.commit("bybit")
        self.assertEqual(len(journal.state()["exchanges"]["bybit"]["trades"]),1)

    def test_credentials_checked_atomic_bound_and_not_in_state(self):
        with patch.object(sync,"Client") as constructor:
            constructor.return_value.identity.return_value="same-account"
            saved=sync.save("okx",CREDENTIALS)
            self.assertTrue(saved["configured"])
            self.assertEqual(sync.config_path().stat().st_mode & 0o777,0o600)
            self.assertNotIn("fixture-secret",json.dumps(journal.state()))
            self.assertNotIn("fixture-key",json.dumps(saved))
            constructor.return_value.identity.return_value="different-account"
            with self.assertRaises(ExchangeError):sync.save("okx",dict(CREDENTIALS,secret="new-secret"))
            self.assertEqual(sync.config()["okx"]["secret"],"fixture-secret")
            constructor.return_value.identity.side_effect=ExchangeError("验证失败")
            with self.assertRaises(ExchangeError):sync.save("bybit",CREDENTIALS)
            self.assertNotIn("bybit",sync.config())

    def test_snapshot_quantity_race_does_not_publish(self):
        sync.write_config("okx",dict(CREDENTIALS,identity="same"))
        c=Mock();c.identity.return_value="same";c.offset=0;c.fetch.return_value=bundle();c.positions.return_value=[]
        with patch.object(sync,"Client",return_value=c):self.assertFalse(sync.collect("okx"))
        self.assertFalse(journal.state()["exchanges"]["okx"]["snapshot"])
        self.assertEqual(journal.state()["journals"],[])

    def test_unconfigured_workers_do_no_network_io(self):
        worker=sync.Worker("bybit")
        with patch.object(sync,"collect") as poll,patch.object(worker.wake,"wait",side_effect=lambda _:worker.stop.set()):worker.run()
        poll.assert_not_called()


class ExchangeHTTPTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        import server
        self.server_module=server
        self.temp=tempfile.TemporaryDirectory();self.old=journal.DB;journal.DB=Path(self.temp.name)/'db.sqlite3';journal.init()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();journal.DB=self.old;self.temp.cleanup()
    def post(self,path,data,token=True):
        from urllib import request,error
        headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.http.server_port}'}
        if token:headers['X-Journal-Token']=self.server_module.TOKEN
        req=request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with request.urlopen(req) as response:return response.status,json.load(response)
        except error.HTTPError as response:return response.code,json.load(response)
    def test_save_requires_csrf_and_rejects_unknown_venue_without_network(self):
        with patch.object(sync,'save') as save:
            self.assertEqual(self.post('/api/exchanges/okx/credentials',CREDENTIALS,False)[0],403)
            self.assertEqual(self.post('/api/exchanges/unknown/credentials',CREDENTIALS)[0],400)
        save.assert_not_called()
    def test_sync_range_validation_and_secret_free_save_response(self):
        with patch.object(sync,'Client') as constructor:
            constructor.return_value.identity.return_value='bound'
            code,data=self.post('/api/exchanges/okx/credentials',CREDENTIALS)
        self.assertEqual(code,200);self.assertTrue(data['configured'])
        self.assertNotIn('fixture-secret',json.dumps(data));self.assertNotIn('fixture-phrase',json.dumps(data))
        with patch.object(sync,'collect') as collect:
            self.assertEqual(self.post('/api/exchanges/okx/sync',{'days':365})[0],400)
            self.assertEqual(self.post('/api/exchanges/okx/sync',{'days':True})[0],400)
        collect.assert_not_called()
