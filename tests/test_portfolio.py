"""Financial invariants for same-period multi-exchange accounting; synthetic only."""
import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import journal
import portfolio as p
import ledger

NOW = 1792100000000
START = NOW - 30*p.DAY


def bill(ident='1', value='10', kind='REALIZED_PNL', asset='USDT', stamp=NOW-1000, symbol='BTCUSDT'):
    return dict(tranId=ident, income=value, incomeType=kind, asset=asset, time=stamp, symbol=symbol)


def account(venue='binance', rows=None, ranges=None):
    return dict(exchange=venue, income=rows if rows is not None else [bill()], trades=[],
                ranges=ranges if ranges is not None else [[START,NOW]],
                snapshot={'captured_at':NOW,'positions':[]},sync={'status':'complete'})


class PortfolioTests(unittest.TestCase):
    def build(self, accounts):return p.build(accounts,START,NOW,NOW)

    def test_three_accounts_same_ids_decimal_signs_and_no_double_fees(self):
        rows=[bill(value='0.1'),bill('2','-0.03','COMMISSION'),bill('3','0.02','FUNDING_FEE'),bill('4','0.01','COMMISSION_REBATE')]
        accounts=[account(venue,copy.deepcopy(rows)) for venue in p.VENUES]
        for a in accounts:
            a['trades']=[dict(symbol='BTCUSDT',id='9007199254740993',orderId='9007199254740995',time=NOW-1000,commission='0.03',realizedPnl='999')]
        result=self.build(accounts)
        self.assertTrue(result['available']);self.assertEqual(Decimal(result['totals']['USDT']['net']),Decimal('0.30'))
        self.assertEqual(result['analytics']['days'][-1]['order_count'],3)
        self.assertEqual(result['audit']['ledger_count'],12)
        self.assertTrue(result['audit']['passed'])
        for name in ('account_sum','day_sum','symbol_sum','component_sum'):
            self.assertEqual(Decimal(result['audit'][name]),Decimal('0.30'))

    def test_duplicate_is_idempotent_but_changed_amount_blocks_public_total(self):
        b=bill();result=self.build([account(rows=[b,dict(b)])])
        self.assertEqual(result['totals']['USDT']['net'],'10')
        result=self.build([account(rows=[b,dict(b,income='12')])])
        self.assertFalse(result['available']);self.assertEqual(result['totals'],{})

    def test_currency_separation_transfers_ignored_rebate_signed(self):
        rows=[bill(),bill('2','-0.001','COMMISSION','BNB'),bill('3','100000','TRANSFER',symbol=''),bill('4','1','COMMISSION_REBATE')]
        r=self.build([account(rows=rows)])
        self.assertEqual(r['totals']['USDT']['net'],'11');self.assertEqual(r['totals']['BNB']['net'],'-0.001')
        self.assertEqual(r['excluded'][0]['amount'],'100000')
        self.assertEqual(Decimal(r['analytics']['summary']['net']),Decimal('11'))

    def test_common_window_restricts_every_venue_and_never_spans_a_gap(self):
        left=account(rows=[bill(stamp=START+1),bill('2','4')],ranges=[[START,START+5],[NOW-2000,NOW]])
        right=account('okx',rows=[bill(value='3')],ranges=[[NOW-1500,NOW-500]])
        r=self.build([left,right])
        self.assertEqual(r['window']['start'],NOW-1500);self.assertEqual(r['window']['end'],NOW-500)
        self.assertTrue(r['window']['shortened']);self.assertEqual(r['totals']['USDT']['net'],'7')

    def test_missing_coverage_is_not_zero_but_complete_empty_period_is_zero(self):
        self.assertFalse(self.build([account(),account('okx',ranges=[])])['available'])
        self.assertFalse(self.build([])['available'])
        self.assertFalse(self.build([account(ranges=[[START,START+5]]),account('okx',ranges=[[NOW-10,NOW]])])['available'])
        r=self.build([account(rows=[]),account('okx',rows=[])])
        self.assertTrue(r['available']);self.assertEqual(r['totals']['USDT']['net'],'0')

    def test_unknown_types_invalid_values_and_conflicting_fills_fail_closed(self):
        for row in [bill(kind='NEW_FEE'),bill(value='NaN'),bill(value='Infinity'),bill(value='oops'),dict(bill(),tranId=None)]:
            with self.subTest(row=row):
                r=self.build([account(rows=[row])]);self.assertFalse(r['available']);self.assertEqual(r['totals'],{})
        a=account();a['trades']=[dict(symbol='BTCUSDT',id='1',orderId='o',time=NOW-1000),dict(symbol='BTCUSDT',id='1',orderId='changed',time=NOW-1000)]
        self.assertFalse(self.build([a])['available'])

    def test_stale_or_paused_positions_do_not_become_current_combined_pnl(self):
        a=account();a['snapshot']['positions']=[dict(symbol='BTCUSDT',unRealizedProfit='1.01')]
        b=account('bybit');b['snapshot']['positions']=[dict(symbol='ETHUSDT',unRealizedProfit='-0.01')]
        self.assertEqual(self.build([a,b])['unrealized'],'1.00')
        b['snapshot']['captured_at']=NOW-100000
        self.assertIsNone(self.build([a,b])['unrealized'])
        self.assertTrue(self.build([a,b])['available'])
        b['snapshot']['captured_at']=NOW;b['enabled']=False
        self.assertIsNone(self.build([a,b])['unrealized'])

    def test_large_values_and_subcent_costs_are_exact(self):
        r=self.build([account(rows=[bill(value='9007199254740993.123456789'),bill('fee','-0.000000001','COMMISSION')])])
        self.assertEqual(r['totals']['USDT']['net'],'9007199254740993.123456788')

    def test_database_scopes_and_unconfigured_venues(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(journal,'DB',Path(directory)/'test.sqlite3'):
            journal.init()
            with journal.connect() as db:
                for venue in ['binance','okx']:
                    key='snapshot' if venue=='binance' else 'exchange:okx:snapshot'
                    journal.set_kv(db,key,{'captured_at':NOW,'positions':[],'coverage':{'start':START,'end':NOW,'complete':True}})
                for venue in ['binance','okx']:ledger.record_coverage(db,venue,START,NOW)
                b=bill()
                db.execute('INSERT INTO income VALUES(?,?,?,?)',(b['incomeType'],b['tranId'],b['time'],json.dumps(b)))
                db.execute('INSERT INTO exchange_income VALUES(?,?,?,?,?)',('okx',b['incomeType'],b['tranId'],b['time'],json.dumps(b)))
            with journal.connect() as db:
                db.execute('BEGIN')
                r=p.report(db,now=NOW)
                self.assertEqual(r['totals']['USDT']['net'],'20')
                self.assertEqual([a['exchange'] for a in r['accounts']],['binance','okx'])
                self.assertEqual(p.report(db,exchanges=['okx'],now=NOW)['totals']['USDT']['net'],'10')
                self.assertFalse(p.report(db,exchanges=['okx','bybit'],now=NOW)['available'])
                for selection in [[],['okx','okx'],['variational']]:
                    with self.assertRaises(ValueError):p.report(db,exchanges=selection,now=NOW)
                with self.assertRaises(ValueError):p.report(db,days=365,now=NOW)

    def test_legacy_coverage_does_not_claim_verified_income(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(journal,'DB',Path(directory)/'db.sqlite3'):
            journal.init()
            with journal.connect() as db:
                journal.set_kv(db,'snapshot',{'coverage':{'start':START,'end':NOW,'complete':True}})
                self.assertFalse(p.report(db,exchanges=['binance'],now=NOW)['available'])
                ledger.record_coverage(db,'binance',NOW-5000,NOW)
                r=p.report(db,exchanges=['binance'],now=NOW)
                self.assertTrue(r['available']);self.assertEqual(r['window']['start'],NOW-5000)

    def test_bad_fills_and_zero_unknown_components_never_pass(self):
        for f in [{}, {'time':None,'symbol':'ETHUSDT','id':'1'}, {'time':NOW,'symbol':None,'id':'1'}]:
            a=account();a['trades']=[f]
            self.assertFalse(self.build([a])['available'])
        self.assertFalse(self.build([account(rows=[dict(bill(kind='NEW_TYPE',value='0'),unclassified=True)])])['available'])

    def test_individual_and_combined_same_rules_and_exact_rounding(self):
        from analytics import build_analytics
        rows=[bill(value='9007199254740993.1234567890123456789'),bill('2','-0.0000000000123456789','COMMISSION'),bill('3','0.1','API_REBATE')]
        totals,_=journal.aggregate_income(rows)
        one=build_analytics(rows,[],{'complete':True,'start':START,'end':NOW})
        combined=self.build([account(rows=rows)])
        self.assertEqual(totals['USDT']['net'],combined['totals']['USDT']['net'])
        self.assertEqual(one['summary']['net'],combined['totals']['USDT']['net'])
        self.assertEqual(Decimal(totals['USDT']['net']),Decimal('9007199254740993.223456789'))
        bad=rows+[bill('unknown','9','NEW_CHARGE')]
        self.assertEqual(journal.aggregate_income(bad)[0],{})
        self.assertFalse(build_analytics(bad,[],{'complete':True,'start':START,'end':NOW})['available'])

    def test_same_id_different_income_types_preserve_rebate_and_trade(self):
        r=self.build([account(rows=[bill(value='3'),bill(value='-1',kind='COMMISSION'),bill(value='0.2',kind='COMMISSION_REBATE')])])
        self.assertTrue(r['available']);self.assertEqual(r['totals']['USDT']['net'],'2.2')
        self.assertEqual(r['analytics']['summary']['net'],'2.2')
        self.assertTrue(r['audit']['passed'])
