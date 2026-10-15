import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import journal
from fixtures import create_journal
import server


class SessionTests(unittest.TestCase):
    def test_restart_rejects_stale_write_before_mutation_and_valid_retry_saves(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(journal,'DB',Path(folder)/'test.sqlite3'):
            journal.init()
            create_journal()
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(http.server_port)
            def post(token,origin):
                req=urllib.request.Request(url+'/api/journals',data=json.dumps({'id':'fixture-eth','thesis':'kept after restart'}).encode(),headers={'Content-Type':'application/json','Origin':origin,'X-Journal-Token':token})
                try:
                    with urllib.request.urlopen(req) as r:return r.status,json.load(r)
                except urllib.error.HTTPError as e:return e.code,json.load(e)
            try:
                with patch.object(server,'TOKEN','rotated-token'):
                    status,body=post('stale-token',url)
                    self.assertEqual((status,body['code']),(403,'SESSION_EXPIRED'))
                    self.assertEqual(journal.state()['journals'][0]['thesis'],'')
                    status,body=post('rotated-token','https://outside.example')
                    self.assertEqual((status,body['code']),(403,'ORIGIN_REJECTED'))
                    self.assertEqual(post('rotated-token',url)[0],200)
                    self.assertEqual(journal.state()['journals'][0]['thesis'],'kept after restart')
            finally:http.shutdown();http.server_close();thread.join()


if __name__=='__main__':unittest.main()
