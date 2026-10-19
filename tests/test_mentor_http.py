import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import journal
import training
import server


class MentorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.temp.name)/'db.sqlite3');self.dbpatch.start()
        journal.init();training.init()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.dbpatch.stop();self.temp.cleanup()

    def request(self,path,body=None,token=None):
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','Origin':self.url,'X-Journal-Token':token or server.TOKEN})
        try:
            with urllib.request.urlopen(request) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_status_system_and_confirm(self):
        status,data=self.request('/api/assistant/status')
        self.assertEqual(status,200);self.assertEqual(data['system']['version'],1);self.assertEqual(len(data['system']['setups']),4)
        status,system=self.request('/api/system')
        self.assertEqual(status,200);self.assertEqual(system['active']['version'],1);self.assertEqual(len(system['history']),1)
        self.assertEqual(self.request('/api/system/confirm',{'account_risk_pct':1.5},token='bad')[0],403)
        self.assertEqual(self.request('/api/system/confirm',{'account_risk_pct':99})[0],400)
        status,v=self.request('/api/system/confirm',{'account_risk_pct':1.5})
        self.assertEqual(status,200);self.assertEqual(v['version'],2);self.assertTrue(v['data']['rules']['confirmed'])


if __name__=='__main__':
    unittest.main()


class MentorReviewHTTPTests(MentorHTTPTests):
    def test_review_and_adopt_routes(self):
        import json as j
        with journal.connect() as db:
            db.execute('INSERT INTO journals VALUES(?,?,?,?)',('j1',1,1,j.dumps(dict(symbol='ETHUSDT',exchange='binance',direction='LONG',status='持仓中',entry_price='100',quantity='1'))))
        status,data=self.request('/api/mentor/review?id=j1')
        self.assertEqual(status,200);self.assertEqual(data['status'],'not_closed')
        self.assertEqual(self.request('/api/mentor/review?id=nope')[0],400)
        self.assertEqual(self.request('/api/mentor/adopt',{'journal_id':'j1'})[0],400)
        self.assertEqual(self.request('/api/mentor/adopt',{'journal_id':'j1'},token='bad')[0],403)
