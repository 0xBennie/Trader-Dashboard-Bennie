import copy
import unittest
from stop_coverage import summarize
from trade_context import normalize_orders

NOW = 1800000000000


def fixture():
    p = dict(symbol='ETHUSDT', positionSide='BOTH', positionAmt='10', entryPrice='100', updateTime=NOW-30000)
    o = dict(order_ref='algo:1', symbol='ETHUSDT', positionSide='BOTH', side='SELL', type='STOP_MARKET',
             triggerPrice='90', quantity='10', status='NEW', closePosition=False, reduceOnly=True)
    return dict(snapshot=dict(captured_at=NOW, positions=[p]), trade_context=dict(ETHUSDT=dict(status='complete', captured_at=NOW, positions=[copy.deepcopy(p)], orders=[o])))


class CoverageTests(unittest.TestCase):
    def result(self, state):
        return summarize(state, NOW)['rows'][0]

    def test_full_and_partial_and_aggregate_quantity(self):
        s=fixture();self.assertEqual(self.result(s)['status'],'full')
        orders=s['trade_context']['ETHUSDT']['orders'];orders[0]['quantity']='4'
        self.assertEqual(self.result(s)['coverage_pct'],'40.0')
        orders.append(dict(orders[0], order_ref='algo:2', quantity='6'))
        self.assertEqual(self.result(s)['status'],'full')

    def test_close_all_caps_coverage_and_deduplicates(self):
        s=fixture();orders=s['trade_context']['ETHUSDT']['orders'];orders[0]['quantity']='4';orders.append(copy.deepcopy(orders[0]))
        self.assertEqual(self.result(s)['status'],'partial')
        orders[0].update(closePosition=True, quantity='0', reduceOnly=False)
        self.assertEqual(self.result(s)['covered_quantity'],'10')

    def test_take_profit_wrong_side_non_reducing_and_expired_excluded(self):
        for changes in [dict(type='TAKE_PROFIT_MARKET'),dict(side='BUY'),dict(reduceOnly=False),dict(status='CANCELED'),dict(positionSide='LONG')]:
            with self.subTest(changes=changes):
                s=fixture();s['trade_context']['ETHUSDT']['orders'][0].update(changes)
                self.assertEqual(self.result(s)['status'],'missing')

    def test_hedge_short_is_separate_from_long(self):
        s=fixture();s['snapshot']['positions'][0].update(positionSide='SHORT',positionAmt='-10')
        ctx=s['trade_context']['ETHUSDT'];ctx['positions']=copy.deepcopy(s['snapshot']['positions'])
        ctx['orders'][0].update(positionSide='LONG',reduceOnly=False)
        self.assertEqual(self.result(s)['status'],'missing')
        ctx['orders'][0].update(positionSide='SHORT',side='BUY')
        self.assertEqual(self.result(s)['status'],'full')

    def test_stale_failed_missing_or_changed_context_is_unknown(self):
        for case in ['snapshot','context','failed','changed','new_cycle','missing']:
            s=fixture();ctx=s['trade_context']['ETHUSDT']
            if case=='snapshot':s['snapshot']['captured_at']=NOW-90001
            if case=='context':ctx['captured_at']=NOW-90001
            if case=='failed':ctx['status']='failed'
            if case=='changed':s['snapshot']['positions'][0]['positionAmt']='11'
            if case=='new_cycle':s['snapshot']['positions'][0]['updateTime']=NOW
            if case=='missing':del ctx['orders']
            with self.subTest(case=case):self.assertEqual(self.result(s)['status'],'unknown')

    def test_malformed_numbers_do_not_report_protection(self):
        for value in ['NaN','Infinity','bad',None]:
            s=fixture();s['trade_context']['ETHUSDT']['orders'][0]['quantity']=value
            self.assertEqual(self.result(s)['status'],'unknown')

    def test_unknown_and_empty_are_distinct_and_journal_plans_do_not_count(self):
        self.assertFalse(summarize({},NOW)['snapshot_fresh'])
        self.assertTrue(summarize({'snapshot':{'captured_at':NOW,'positions':[]}},NOW)['snapshot_fresh'])
        s=fixture();s['journals']=[dict(symbol='ETHUSDT',stop_loss='90')];s['trade_context']['ETHUSDT']['orders']=[]
        self.assertEqual(self.result(s)['status'],'missing')

    def test_normalization_retains_identity_and_only_remaining_quantity(self):
        order=normalize_orders([dict(symbol='ETHUSDT',orderId=2,origQty='10',executedQty='3',status='PARTIALLY_FILLED')])[0]
        self.assertEqual(order['quantity'],'7');self.assertEqual(order['order_ref'],'order:2')
        self.assertEqual(normalize_orders([dict(symbol='ETHUSDT',algoId=2)])[0]['order_ref'],'algo:2')
