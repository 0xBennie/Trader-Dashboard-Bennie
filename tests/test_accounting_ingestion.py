import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import journal
import ledger
import recorder
import binance_account
import exchange_sync
from binance_client import Binance, BinanceError
from exchange_clients import ExchangeError
from test_exchanges import client

class AccountingIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=patch.object(journal,'DB',self.root/'journal.sqlite3');self.db.start();journal.init()
    def tearDown(self):self.db.stop();self.temp.cleanup()

    def test_uid_binding_rotation_rejects_other_account_and_preserves_credentials(self):
        lock=threading.Lock()
        with patch.object(binance_account,'Binance') as cls:
            cls.return_value.identity.return_value='account-a'
            binance_account.save(self.root,'key-a','secret-a',lock)
            binance_account.save(self.root,'key-b','secret-b',lock)
            before=(self.root/'.env.local').read_bytes()
            cls.return_value.identity.return_value='account-b'
            with self.assertRaises(ValueError):binance_account.save(self.root,'key-c','secret-c',lock)
            self.assertEqual(before,(self.root/'.env.local').read_bytes())
            self.assertEqual((self.root/'.env.local').stat().st_mode&0o777,0o600)
        self.assertFalse(lock.locked())
        with patch.object(Binance,'get',return_value={'uid':123}):
            self.assertEqual(Binance().identity(),hashlib.sha256(b'binance:123').hexdigest())
        with patch.object(Binance,'get',return_value={}):
            with self.assertRaises(BinanceError):Binance().identity()

    def test_recorder_revalidates_legacy_window_and_retains_actual_start(self):
        now=1792100000000
        with journal.connect() as db:
            journal.set_kv(db,'snapshot',{'coverage':{'start':now-30*86400000,'end':now-1000,'days':30,'complete':True}})
        c=Mock(key='fixture',secret='fixture',offset=0);c.identity.return_value='account-a'
        c.get.return_value=[];c.income.return_value=[];c.trades.return_value=[]
        w=recorder.Recorder(threading.Lock())
        with patch.object(recorder,'credentials',return_value=('fixture','fixture')),patch.object(recorder,'Binance',return_value=c),patch.object(journal,'now_ms',return_value=now),patch.object(w,'capture_market'),patch.object(recorder.trade_context,'collect',return_value={}):
            w.poll()
        self.assertEqual(c.income.call_args.args,(now-30*86400000,now))
        with journal.connect() as db:self.assertEqual(ledger.ranges(db,'binance'),[[now-30*86400000,now]])
        later=now+120000
        with patch.object(recorder,'credentials',return_value=('fixture','fixture')),patch.object(journal,'now_ms',return_value=later),patch.object(w,'capture_market'),patch.object(recorder.trade_context,'collect',return_value={}):w.poll()
        self.assertEqual(c.income.call_args.args,(later-7*86400000,later))
        with journal.connect() as db:self.assertEqual(ledger.ranges(db,'binance'),[[now-30*86400000,later]])

    def test_raw_bybit_components_reconcile_and_unknown_types_survive(self):
        c=client('bybit')
        base=dict(symbol='XYZUSDT',id='1',currency='USDT',type='TRADE',transactionTime='1000',cashFlow='10',fee='0.2',funding='-0.5',change='9.3')
        rows=c.income([base,base]);totals,_=journal.aggregate_income(rows)
        self.assertEqual(totals['USDT']['net'],'9.3');self.assertEqual(len(rows),3)
        for value in ['9','NaN']:
            with self.assertRaises(ExchangeError):c.income([dict(base,change=value)])
        with self.assertRaises(ExchangeError):c.income([base,dict(base,cashFlow='11',change='10.3')])
        unknown=c.income([dict(base,type='NEW_FEE')])
        self.assertTrue(unknown[0]['unclassified']);self.assertEqual(journal.aggregate_income(unknown)[0],{})
        refund=c.income([dict(base,id='refund',symbol='',type='FEE_REFUND',cashFlow='0.2',fee='0',funding='0',change='0.2')])
        self.assertEqual(journal.aggregate_income(refund)[0]['USDT']['fees'],'0.2')

    def test_collection_cannot_replace_durable_account_binding(self):
        with journal.connect() as db:
            journal.set_kv(db,'exchange:bybit:identity','original-account')
        with patch.object(exchange_sync,'config',return_value={'bybit':{'identity':'replacement-account'}}),patch.object(exchange_sync,'Client') as cls:
            cls.return_value.identity.return_value='replacement-account'
            self.assertFalse(exchange_sync.collect('bybit'))
            cls.return_value.fetch.assert_not_called()
        self.assertEqual(journal.kv('exchange:bybit:identity'),'original-account')
        self.assertIn('身份',journal.kv('exchange:bybit:status')['error'])

    def test_okx_unknown_and_cross_margin_mismatch_are_not_dropped(self):
        c=client('okx')
        b=dict(instId='XYZ-USDT-SWAP',ccy='USDT',ts='1000',billId='1',type='2',pnl='5',fee='-0.2',balChg='4.8',mgnMode='cross')
        self.assertEqual(journal.aggregate_income(c.income([b]))[0]['USDT']['net'],'4.8')
        with self.assertRaises(ExchangeError):c.income([dict(b,balChg='5')])
        with self.assertRaises(ExchangeError):c.income([b,dict(b,pnl='6',balChg='5.8')])
        self.assertTrue(c.income([dict(b,type='999')])[0]['unclassified'])
        self.assertTrue(c.income([dict(b,instId='',type='999')])[0]['unclassified'])

    def test_resync_replaces_old_components_only_inside_fetched_window(self):
        old={'symbol':'XYZUSDT','asset':'USDT','time':1000,'incomeType':'COMMISSION','income':'-9','tranId':'old'}
        outside=dict(old,time=100,tranId='outside',income='-1')
        with journal.connect() as db:
            for row in (old,outside):
                db.execute('INSERT INTO exchange_income VALUES(?,?,?,?,?)',('okx',row['incomeType'],row['tranId'],row['time'],json.dumps(row)))
        fresh=dict(old,income='-0.2',tranId='new')
        result={'positions':[],'orders':[],'trades':[],'income':[fresh],'wallet':{},'instruments':{},'raw':{}}
        with patch.object(recorder,'reconcile',return_value=([],[])),patch.object(recorder,'capture_positions'):
            exchange_sync.commit('okx',result,500,2000,2000)
            exchange_sync.commit('okx',result,500,2000,2000)
        with journal.connect() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT raw FROM exchange_income')]
            self.assertEqual(ledger.ranges(db,'okx'),[[500,2000]])
        self.assertEqual({r['tranId'] for r in rows},{'outside','new'})
        self.assertEqual(journal.aggregate_income(rows)[0]['USDT']['net'],'-1.2')
