"""Isolated API acceptance: no production policy, permit, or exchange order is created."""
import json
import fcntl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import journal
import training
import backtest
import pretrade_ai
import server


def evidence(_):
    stamp=journal.now_ms()
    return {'captured_at':stamp,'errors':[],
            'ledger':{'captured_at':stamp,'complete':True,'day_start':training.day_bounds(stamp)[1],'through':stamp},
            'quote':{'symbol':'ETHUSDT','price':'100','captured_at':stamp},
            'positions':{'captured_at':stamp,'rows':[],'complete':True},
            'orders':{'captured_at':stamp,'rows':[],'complete':True},
            'account':{'captured_at':stamp,'complete':True,'daily_realized_pnl_usdt':'0','day_start':training.day_bounds(stamp)[1]}}


class HTTPTrainingTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.folder.name)/'db.sqlite3');self.dbpatch.start()
        journal.init();training.init();backtest.init();pretrade_ai.init()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        self.dbpatch.stop();self.folder.cleanup()

    def request(self,path,body=None,method=None,token=None):
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
            method=method,headers={'Content-Type':'application/json','Origin':self.url,'X-Journal-Token':token or server.TOKEN})
        try:
            with urllib.request.urlopen(request) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_full_review_confirmation_edit_and_version_conflict(self):
        policy={'version':0,'confirmed':True,'max_trade_risk_usdt':'20','max_daily_loss_usdt':'50',
                'max_entries_per_day':3,'cooldown_seconds':60}
        self.assertEqual(self.request('/api/v1/risk-policy',policy,method='PUT')[0],200)
        body={'version':0,'symbol':'ETHUSDT','direction':'LONG','entry_price':'100','quantity':'1',
              'stop_loss':'95','take_profit':'110','thesis':'测试明确依据','invalidation':'跌破预先定义区间','emotion':'calm'}
        status,plan=self.request('/api/v1/intents',body);self.assertEqual(status,200)
        root='/api/v1/intents/'+plan['id']
        ai={'verdict':'support','summary':'隔离验收结果','supporting':['测试证据完整'],'opposing':['仍有风险'],
            'unknowns':['盘内路径'],'invalidation':'测试止损失效','next_step':'本人核对'}
        with patch.object(server.pretrade_evidence,'collect',side_effect=evidence),patch.object(pretrade_ai,'model_json',return_value=ai),patch.object(pretrade_ai.ai_review,'status',return_value={'available':True,'model':'fixture'}):
            status,reviewing=self.request(root+'/review',{'version':plan['version']});self.assertEqual(status,202)
            for _ in range(100):
                current=self.request(root)[1]
                if current['status']!='reviewing':break
                time.sleep(.01)
            self.assertEqual(current['status'],'awaiting_confirmation',current)
            payload={'version':current['version'],'idempotency_key':'isolated-acceptance-key'}
            status,approved=self.request(root+'/confirm',payload);self.assertEqual(status,200,approved)
            again=self.request(root+'/confirm',payload)[1]
            self.assertEqual(approved['permit']['id'],again['permit']['id'])
            status,edited=self.request('/api/v1/intents',dict(approved,quantity='2'))
            self.assertEqual(status,200,edited);self.assertEqual(edited['status'],'draft')
            self.assertIsNone(edited['review']);self.assertIsNone(edited['permit'])
            self.assertEqual(self.request('/api/v1/intents',dict(approved,quantity='3'))[0],409)
        status,today=self.request('/api/v1/training/today');self.assertEqual(status,200)
        self.assertEqual(today['metrics']['pretrade_coverage']['denominator'],0)

    def test_policy_is_not_implicitly_enabled_and_bad_session_cannot_write(self):
        self.assertFalse(self.request('/api/v1/risk-policy')[1]['configured'])
        status,result=self.request('/api/v1/intents',{'version':0},token='old-token')
        self.assertEqual((status,result['code']),(403,'SESSION_EXPIRED'))
        self.assertEqual(self.request('/api/v1/intents')[1]['items'],[])
        for path in ['/api/v1/backtests', '/api/v1/backtests/missing', '/api/v1/strategies', '/api/v1/chart-hypothesis']:
            self.assertEqual(self.request(path)[0],410)
            self.assertEqual(self.request(path, {})[0],410)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM training_permits').fetchone()[0],0)

    def test_duplicate_start_does_not_recover_running_tasks(self):
        lock=open(journal.DB.with_suffix('.server.lock'),'a')
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            with patch('sys.argv',['server.py','--port','0']),patch.object(training,'init') as recover:
                with self.assertRaises(SystemExit):server.main()
                recover.assert_not_called()
        finally:lock.close()

    def test_port_failure_happens_before_task_recovery(self):
        with patch('sys.argv',['server.py','--port',str(self.http.server_port)]),patch.object(training,'init') as recover:
            with self.assertRaises(OSError):server.main()
            recover.assert_not_called()


if __name__=='__main__':unittest.main()
