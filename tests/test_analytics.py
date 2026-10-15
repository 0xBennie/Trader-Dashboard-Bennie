import unittest
from datetime import datetime
from decimal import Decimal
from analytics import build_analytics


def stamp(value):
    return int(datetime.fromisoformat(value).timestamp()*1000)


class AnalyticsTests(unittest.TestCase):
    def test_hong_kong_midnight_fees_and_missing_symbol_reconcile(self):
        start=stamp('2026-10-14T00:00:00+08:00');end=stamp('2026-10-15T12:00:00+08:00')
        rows=[dict(asset=a,incomeType=k,income=v,symbol=s,time=stamp(t)) for a,k,v,s,t in [
            ('USDT','REALIZED_PNL','10','ETHUSDT','2026-10-14T23:59:59+08:00'),
            ('USDT','COMMISSION','-1','ETHUSDT','2026-10-15T00:00:00+08:00'),
            ('USDT','FUNDING_FEE','-2','','2026-10-15T01:00:00+08:00'),
            ('BNB','COMMISSION','-9','ETHUSDT','2026-10-15T01:00:00+08:00'),
            ('USDT','TRANSFER','10000','','2026-10-15T01:00:00+08:00')]]
        fills=[dict(symbol='ETHUSDT',id=i,orderId='9007199254740993',time=end) for i in range(2)]
        result=build_analytics(rows,fills,dict(start=start,end=end,complete=True))
        self.assertEqual([r['net'] for r in result['days']],['10','-3'])
        self.assertEqual(result['days'][1]['order_count'],1)
        self.assertEqual(result['days'][1]['fill_count'],2)
        self.assertEqual(result['summary']['max_ledger_drawdown'],'3')
        self.assertEqual(result['summary']['net'],'7')
        self.assertEqual(sum(Decimal(s['net']) for s in result['symbols']),Decimal('7'))
        self.assertFalse(result['days'][0]['partial']);self.assertTrue(result['days'][1]['partial'])

    def test_zero_days_are_retained_but_not_positive_or_negative_days(self):
        start=stamp('2026-10-14T00:00:00+08:00');end=stamp('2026-10-16T01:00:00+08:00')
        result=build_analytics([],[],dict(start=start,end=end,complete=True))
        self.assertEqual(len(result['days']),3)
        self.assertEqual(result['summary']['active_days'],0)
        self.assertIsNone(result['summary']['best'])
        self.assertFalse(build_analytics([],[],{})['available'])


if __name__=='__main__':unittest.main()
