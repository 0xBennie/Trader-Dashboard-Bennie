import json
from unittest.mock import Mock, patch
from test_review_learning_http import ReviewHTTPTests
import journal
import ledger

class PortfolioHTTPTests(ReviewHTTPTests):
    # Inherit only the isolated server fixture, not the unrelated review roundtrip.
    test_roundtrip_export_and_bad_requests = None

    def test_readonly_report_parameters_auth_and_identical_state_total(self):
        now=journal.now_ms()
        with journal.connect() as db:
            for venue in ('binance','okx'):
                ledger.record_coverage(db,venue,now-86400000,now)
                key='snapshot' if venue=='binance' else 'exchange:okx:snapshot'
                journal.set_kv(db,key,{'captured_at':now,'positions':[]})
                b={'symbol':'ETHUSDT','tranId':'same-id','time':now-100,'incomeType':'REALIZED_PNL','income':'0.1','asset':'USDT'}
                if venue=='binance':db.execute('INSERT INTO income VALUES(?,?,?,?)',(b['incomeType'],b['tranId'],b['time'],json.dumps(b)))
                else:db.execute('INSERT INTO exchange_income VALUES(?,?,?,?,?)',(venue,b['incomeType'],b['tranId'],b['time'],json.dumps(b)))
        with patch.object(journal,'now_ms',return_value=now):
            code,result=self.request('/api/portfolio');self.assertEqual(code,200,result)
            self.assertEqual(result['totals']['USDT']['net'],'0.2')
            self.assertEqual(self.request('/api/state')[1]['portfolio']['totals'],result['totals'])
            self.assertEqual(self.request('/api/portfolio?exchanges=okx')[1]['totals']['USDT']['net'],'0.1')
        for query in ('days=x','days=365','days=7&days=30','exchanges=','exchanges=binance,binance','exchanges=unknown','secret=bad'):
            self.assertEqual(self.request('/api/portfolio?'+query)[0],400,query)
        self.http.auth=Mock(enabled=True,public_origin='',session=Mock(return_value=None))
        self.assertEqual(self.request('/api/portfolio')[0],401)
