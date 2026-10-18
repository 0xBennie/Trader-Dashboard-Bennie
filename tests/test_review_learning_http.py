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


class ReviewHTTPTests(unittest.TestCase):
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

    def test_roundtrip_export_and_bad_requests(self):
        import review_automation
        review_automation.ensure_daily()
        status,daily=self.request('/api/review/daily')
        self.assertEqual(status,200)
        self.assertEqual(len(daily['reports']),1)
        self.assertEqual(self.request('/api/review/retry',{},token='bad-token')[0],403)
        self.assertEqual(self.request('/api/review/retry',{})[1]['queued'],0)
        status,j=self.request('/api/journals',{'symbol':'ETHUSDT','review_next':'先确认失效条件'})
        self.assertEqual(status,200)
        status,action=self.request('/api/review/actions',{'journal_id':j['id']});self.assertEqual(status,200)
        self.assertEqual(self.request('/api/review/learning?days=90&exchange=binance')[1]['actions'][0]['id'],action['id'])
        now=journal.now_ms()
        status,target=self.request('/api/journals',{'symbol':'BTCUSDT','status':'已平仓','opened_at':'2099-01-01T00:00'})
        self.assertEqual(self.request('/api/review/practices',dict(action_id=action['id'],journal_id=target['id'],outcome='followed',note='核对后执行'))[0],400)
        with journal.connect() as db:
            row=json.loads(db.execute('SELECT data FROM journals WHERE id=?',(target['id'],)).fetchone()[0])
            row['execution']={'opened_ms':now,'closed_ms':now+1}
            db.execute('UPDATE journals SET data=? WHERE id=?',(json.dumps(row),target['id']))
        with patch.object(journal,'now_ms',return_value=now+2):
            status,p=self.request('/api/review/practices',dict(action_id=action['id'],journal_id=target['id'],outcome='followed',note='核对后执行'))
            self.assertEqual(status,200,p)
            self.assertEqual(self.request('/api/review/practices/undo',{'id':p['id']})[0],200)
        self.assertEqual(self.request('/api/review/actions/archive',{'id':action['id'],'archived':True})[0],200)
        status,export=self.request('/api/export');self.assertEqual(status,200,export)
        self.assertEqual(export['review_practices'][0]['voided'],1)
        self.assertEqual(export['review_actions'][0]['archived'],1)
        for path in ('/api/review/learning?days=x','/api/review/learning?days=365','/api/review/learning?exchange=unknown'):
            self.assertEqual(self.request(path)[0],400,path)
        for path in ('/api/review/actions','/api/review/actions/archive','/api/review/practices','/api/review/practices/undo'):
            self.assertEqual(self.request(path,{},token='bad-token')[0],403,path)
            self.assertEqual(self.request(path,{'id':[],'journal_id':{},'outcome':[]})[0],400,path)
