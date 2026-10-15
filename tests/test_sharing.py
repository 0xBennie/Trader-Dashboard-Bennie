import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import journal
import sharing
import auth
import server


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=sharing.HK).timestamp() * 1000)


class SharingFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(journal, 'DB', Path(self.temp.name) / 'test.sqlite3'); self.dbpatch.start()
        self.clock = patch.object(journal, 'now_ms', return_value=ms('2026-10-16T12:00:00')); self.now = self.clock.start()
        journal.init()
        self.cfg = {'mode': 'month', 'period': '2026-10', 'exchange': 'binance', 'nickname': '<b>Trader</b>',
                    'note': 'PRIVATE_NOTE_HERE', 'show_note': False, 'show_amounts': False, 'show_details': False}

    def tearDown(self):
        self.clock.stop(); self.dbpatch.stop(); self.temp.cleanup()

    def cover(self, venue='binance', start='2026-10-01T00:00:00', end='2026-10-16T12:00:00'):
        with journal.connect() as db:
            journal.set_kv(db, 'snapshot' if venue == 'binance' else f'exchange:{venue}:snapshot', {'coverage': {'start': ms(start), 'end': ms(end), 'complete': True}})

    def ledger(self, value='12345.6789', venue='binance', when='2026-10-03T08:00:00', kind='REALIZED_PNL', asset='USDT', key='1'):
        raw = {'time': ms(when), 'income': value, 'asset': asset, 'incomeType': kind, 'symbol': 'SECRETUSDT', 'tranId': key}
        with journal.connect() as db:
            if venue == 'binance':
                db.execute('INSERT OR REPLACE INTO income VALUES(?,?,?,?)', (kind,key,raw['time'],json.dumps(raw)))
            else:
                db.execute('INSERT OR REPLACE INTO exchange_income VALUES(?,?,?,?,?)', (venue,kind,key,raw['time'],json.dumps(raw)))

    def trade(self, jid='test-private-id', closed='2026-10-03T10:00:00', updated='2026-10-04T10:00:00', reflection='secret reflection', complete=True, venue='binance', cycle='private-cycle'):
        data = {'symbol': 'SECRETUSDT', 'exchange': venue, 'direction': 'LONG', 'status': '已平仓',
                'reflection': reflection, 'thesis': 'PRIVATE_THESIS', 'entry_price': '12345.6789', 'quantity': '987.654321',
                'execution': {'cycle_id':cycle, 'history_complete': complete, 'closed_ms': ms(closed), 'opened_ms':ms('2026-10-02T10:00:00'),
                              'entry_price':'12345.6789', 'realized_pnl':'12345.6789', 'fees':{'USDT':'4.12'}, 'fills':['private-order'],
                              'entry_quantity':'987.654321'}}
        with journal.connect() as db:
            db.execute('INSERT OR REPLACE INTO journals VALUES(?,?,?,?)', (jid,ms('2026-10-02T10:00:00'),ms(updated),json.dumps(data)))
        return data

    def report(self, **kw):
        return sharing.preview(dict(self.cfg, **kw))['report']


class SharingTests(SharingFixture, unittest.TestCase):
    def test_hidden_values_are_absent_from_entire_report_including_chart_data(self):
        self.cover();self.ledger();self.trade()
        result=self.report();encoded=json.dumps(result)
        for value in ['12345.6789','987.654321','PRIVATE_NOTE_HERE','secret reflection','PRIVATE_THESIS','private-order','test-private-id','SECRETUSDT']:
            self.assertNotIn(value,encoded)
        self.assertNotIn('net',result)
        self.assertTrue(all('net' not in d for d in result['days']+result['bars']))
        self.assertEqual(result['summary']['closed'],1)
        self.assertEqual(result['summary']['reviewed'],1)
        self.assertEqual(result['details'],[])

    def test_ledger_sources_fees_and_partial_days(self):
        self.cover();self.cover('okx');self.ledger('10');self.ledger('-1',kind='COMMISSION',key='2');self.ledger('-2',kind='FUNDING_FEE',key='3')
        self.ledger('100',venue='okx');self.ledger('100000',kind='TRANSFER',key='4');self.ledger('99999',asset='BTC',key='5')
        result=self.report(show_amounts=True)
        self.assertEqual(result['net'],'7')
        self.assertEqual(self.report(exchange='okx',show_amounts=True)['net'],'100')
        days={d['date']:d for d in result['days']}
        self.assertEqual(days['2026-10-16']['coverage'],'partial')
        self.assertEqual(days['2026-10-17']['coverage'],'future')
        self.assertEqual(days['2026-10-02']['net'],'0')

    def test_unknown_days_are_not_zero_and_history_survives_shorter_sync(self):
        self.cover(start='2026-10-02T12:00:00',end='2026-10-04T23:59:59.999')
        self.cover(start='2026-10-08T00:00:00',end='2026-10-09T23:59:59.999')
        self.cover(start='2026-10-03T00:00:00',end='2026-10-03T01:00:00')
        self.ledger('99999',when='2026-10-01T12:00:00')
        result=self.report(show_amounts=True)
        days={d['date']:d for d in result['days']}
        self.assertEqual(days['2026-10-01']['coverage'],'unknown');self.assertNotIn('net',days['2026-10-01'])
        self.assertEqual(days['2026-10-02']['coverage'],'partial')
        self.assertEqual(days['2026-10-03']['coverage'],'full')
        self.assertEqual(days['2026-10-06']['coverage'],'unknown')
        self.assertEqual(len(result['coverage']),2);self.assertEqual(result['net'],'0')
        self.cover(start='2026-10-04T00:00:00',end='2026-10-08T01:00:00')
        self.assertEqual(len(self.report()['coverage']),1)

    def test_count_cycles_not_fills_and_exclude_incomplete(self):
        self.trade();self.trade(jid='duplicate',cycle='private-cycle')
        self.trade(jid='partial',cycle='partial-cycle',complete=False)
        result=self.report(show_details=True)
        self.assertEqual(result['summary']['closed'],1);self.assertEqual(result['summary']['excluded'],1)
        self.assertEqual(len(result['details']),1)
        self.assertNotIn('entry_price',result['details'][0])

    def test_first_post_close_review_not_unrelated_edits_and_not_counted_twice(self):
        old=self.trade(reflection='before close',updated='2026-10-02T12:00:00')
        with journal.connect() as db:
            db.execute('INSERT INTO revisions(journal_id,time,data) VALUES(?,?,?)',('test-private-id',ms('2026-10-02T12:00:00'),json.dumps(old)))
            db.execute('UPDATE journals SET updated=? WHERE id=?',(ms('2026-10-04T10:00:00'),'test-private-id'))
        self.assertEqual(self.report()['summary']['reviewed'],0)
        journal.save_journal({'id':'test-private-id','reflection':'first review'})
        self.now.return_value=ms('2026-10-17T12:00:00')
        journal.save_journal({'id':'test-private-id','reflection':'improved review'})
        r=self.report();days={d['date']:d for d in r['days']}
        self.assertEqual(days['2026-10-16']['reviews'],1);self.assertEqual(days['2026-10-17']['reviews'],0)
        self.assertEqual(r['summary']['review_days'],1)

    def test_cross_period_reviews_do_not_change_closed_cohort(self):
        self.trade(closed='2026-09-30T23:59:00',updated='2026-10-01T00:00:00')
        r=self.report();self.assertEqual(r['summary']['closed'],0);self.assertEqual(r['summary']['reviewed'],0)
        self.assertEqual(r['days'][0]['reviews'],1)

    def test_leap_year_hong_kong_boundary_and_year_aggregation(self):
        self.cover(start='2024-02-01T00:00:00',end='2024-03-01T00:00:00')
        self.ledger('5',when='2024-02-29T23:59:59')
        r=self.report(mode='year',period='2024',show_amounts=True)
        self.assertEqual(len(r['days']),366);self.assertEqual(len(r['bars']),12)
        self.assertEqual(next(b['net'] for b in r['bars'] if b['key']=='2024-02'),'5')
        self.assertEqual(sharing.date_key(ms('2026-10-03T00:00:00')),'2026-10-03')

    def test_single_trade_manual_source_no_invented_realized_pnl(self):
        self.trade(venue='variational')
        r=self.report(mode='trade',exchange='variational',journal_id='test-private-id',show_amounts=True,show_note=True)
        self.assertNotIn('gross_pnl',r['trade']);self.assertEqual(r['note'],'PRIVATE_NOTE_HERE')
        self.assertNotIn('secret reflection',json.dumps(r))
        hidden=self.report(mode='trade',exchange='variational',journal_id='test-private-id')
        self.assertNotIn('12345.6789',json.dumps(hidden));self.assertIn('手动',hidden['source'])
        with self.assertRaises(ValueError):self.report(mode='trade',exchange='okx',journal_id='test-private-id')

    def test_public_snapshot_is_immutable_and_idempotent_and_revocable(self):
        self.cover();self.ledger('10')
        draft=sharing.preview(dict(self.cfg,show_amounts=True))
        self.ledger('999')
        published=sharing.publish({'draft_id':draft['draft_id'],'confirmed':True})
        token=published['path'].split('/')[-1]
        self.assertEqual(sharing.public_report(token)['net'],'10')
        self.assertEqual(sharing.publish({'draft_id':draft['draft_id'],'confirmed':True}),published)
        self.assertIsNone(sharing.public_report(draft['draft_id']))
        sharing.revoke({'id':published['id']})
        self.assertIsNone(sharing.public_report(token));self.assertFalse(sharing.listing()['items'][0]['active'])
        with self.assertRaises(ValueError):sharing.publish({'draft_id':draft['draft_id'],'confirmed':True})
        with journal.connect() as db:self.assertEqual(db.execute('SELECT report FROM shared_reports WHERE id=?',(published['id'],)).fetchone()[0],'{}')

    def test_expiry_and_publication_require_explicit_preview_and_consent(self):
        draft=sharing.preview(self.cfg)
        for body in [{'draft_id':draft['draft_id']},{'draft_id':'missing','confirmed':True},{'draft_id':draft['draft_id'],'confirmed':True,'days':True}]:
            with self.assertRaises(ValueError):sharing.publish(body)
        result=sharing.publish({'draft_id':draft['draft_id'],'confirmed':True,'days':7})
        self.now.return_value=result['expires']
        self.assertIsNone(sharing.public_report(result['path'].split('/')[-1]))
        for payload in [{'period':'2026-13'},{'period':'2027-01'},{'show_amounts':'false'},{'nickname':'x'*33}]:
            with self.assertRaises(ValueError):self.report(**payload)

    def test_reinitialize_backfills_existing_coverage_without_changing_account_records(self):
        self.cover();self.trade()
        journal.init();journal.init()
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM share_coverage').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM journals').fetchone()[0],1)

    def test_detail_payload_contains_only_fields_shown_in_card_and_execution_wins_over_note_edits(self):
        data=self.trade()
        data['symbol']='EDITEDUSDT';data['direction']='SHORT'
        data['execution'].update(symbol='FACTUSDT',direction='LONG')
        with journal.connect() as db:
            db.execute('UPDATE journals SET data=?',(json.dumps(data),))
        r=self.report(show_amounts=True,show_details=True)
        self.assertEqual(set(r['details'][0]),{'symbol','direction','closed','gross_pnl'})
        self.assertEqual(r['details'][0]['symbol'],'FACTUSDT')
        self.assertEqual(r['details'][0]['direction'],'LONG')

    def test_recent_expired_links_cannot_hide_older_active_links_from_management(self):
        draft=sharing.preview(self.cfg)
        active=sharing.publish({'draft_id':draft['draft_id'],'confirmed':True})
        with journal.connect() as db:
            for i in range(205):
                db.execute('INSERT INTO shared_reports VALUES(?,?,?,?,?,?,?)',
                           (f'revoked-{i}',f'token-{i}',journal.now_ms(),journal.now_ms()+i+1,journal.now_ms()+1000,journal.now_ms(),'{}'))
        self.assertEqual(sharing.listing()['items'][0]['id'],active['id'])


class SharingHTTPTests(SharingFixture, unittest.TestCase):
    # Reuse synthetic ledger setup; only this method starts an HTTP server.
    def test_public_routes_are_isolated_from_private_account_and_revocation_enforced(self):
        account_path=Path(self.temp.name)/'auth.json';auth.save_account(account_path,'reader','synthetic-password-123')
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);http.auth=auth.Auth(account_path)
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        url=f'http://127.0.0.1:{http.server_port}'
        def request(path,payload=None,cookie='',csrf=''):
            req=urllib.request.Request(url+path,data=json.dumps(payload).encode() if payload is not None else None,
                headers={'Origin':url,'Content-Type':'application/json','Cookie':cookie,'X-Journal-Token':csrf})
            try:response=urllib.request.urlopen(req)
            except urllib.error.HTTPError as error:response=error
            with response:
                body=response.read();return response.status,response.headers,json.loads(body) if response.headers.get('Content-Type','').startswith('application/json') else body
        try:
            for path in ['/api/shares','/api/state','/share-workbench.js','/api/export']:
                self.assertEqual(request(path)[0],401)
            self.assertEqual(request('/api/shares/preview',self.cfg)[0],401)
            _,headers,_=request('/api/login',{'username':'reader','password':'synthetic-password-123'})
            cookie=headers['Set-Cookie'].split(';')[0];csrf=http.auth.session(cookie,False)['csrf']
            self.assertEqual(request('/api/shares/preview',self.cfg,cookie,'wrong')[0],403)
            status,_,draft=request('/api/shares/preview',self.cfg,cookie,csrf);self.assertEqual(status,200)
            status,_,published=request('/api/shares/publish',{'draft_id':draft['draft_id'],'confirmed':True},cookie,csrf);self.assertEqual(status,200)
            status,headers,body=request(published['path']);self.assertEqual(status,200)
            self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn('noindex',headers['X-Robots-Tag'])
            self.assertIn(b'share-render.js',body)
            public='/api/public/shares/'+published['path'].split('/')[-1]
            self.assertEqual(request(public)[2],draft['report']);self.assertNotIn('csrf',request(public)[2])
            self.assertEqual(request('/api/shares/revoke',{'id':published['id']})[0],401)
            self.assertEqual(request('/api/shares/revoke',{'id':published['id']},cookie,csrf)[0],200)
            self.assertEqual(request(public)[0],404);self.assertEqual(request(published['path'])[0],404)
            self.assertEqual(request('/s/../../api/state')[0],404)
        finally:http.shutdown();http.server_close();worker.join()

if __name__=='__main__':unittest.main()
