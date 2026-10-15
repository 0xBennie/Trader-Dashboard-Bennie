"""User-authored learning with explicit provenance; no network or exchange writes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import journal
import review_learning as learning
import sharing

DAY = 86400000
NOW = 1800000000000


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(journal, 'DB', Path(self.folder.name) / 'db.sqlite3')
        self.dbpatch.start()
        self.clock = patch.object(journal, 'now_ms', return_value=NOW)
        self.clock.start()
        journal.init()

    def tearDown(self):
        self.clock.stop(); self.dbpatch.stop(); self.folder.cleanup()

    def record(self, jid='source', exchange='binance', opened=NOW-3*DAY, closed=NOW-DAY, updated=NOW, **kwargs):
        data = dict(symbol='ETHUSDT', exchange=exchange, status='已平仓', direction='LONG',
                    execution=dict(opened_ms=opened, closed_ms=closed), **kwargs)
        with journal.connect() as db:
            db.execute('INSERT INTO journals VALUES(?,?,?,?)', (jid, opened, updated, json.dumps(data)))
        return dict(data, id=jid, created=opened, updated=updated)

    def test_validation_and_normalization(self):
        data = learning.fields({'review_keep':'  按计划离场  ', 'review_mistakes':'stop,size,stop'}, {})
        self.assertEqual(data['review_keep'], '按计划离场')
        self.assertEqual(data['review_mistakes'], 'size,stop')
        for values in ({'review_mistakes':['stop']}, {'review_mistakes':'unsupported'}, {'review_execution':[]}, {'review_next':'x'*2001}):
            with self.assertRaises(ValueError): learning.fields(values,{})
        self.assertEqual(learning.fields({},data),data)
        with self.assertRaises(ValueError): learning.practice({'outcome':[]})
        with self.assertRaises(ValueError): learning.create_action({'journal_id':{}})

    def test_tags_alone_do_not_complete_review_and_text_does(self):
        item = self.record(review_mistakes='chasing',review_execution='deviated')
        self.assertEqual(learning.report()['reviewed_count'],0)
        journal.save_journal({'id':item['id'],'review_next':'先按止损算仓位'})
        result=learning.report()
        self.assertEqual((result['reviewed_count'],result['structured_count']),(1,1))
        self.assertEqual(result['patterns'][0]['key'],'chasing')
        with journal.connect() as db:
            saved=journal.all_journals(db)[0]
            self.assertEqual(sharing.first_review(db,saved),NOW)

    def test_unrelated_edit_or_deleting_an_old_field_cannot_complete_review(self):
        item=self.record(updated=NOW-2*DAY,reflection='盘中观察',review_keep='盘中已写的做法')
        journal.save_journal({'id':item['id'],'title':'改标题','review_execution':'followed'})
        self.assertEqual(learning.report()['reviewed_count'],0)
        journal.save_journal({'id':item['id'],'reflection':''})
        self.assertEqual(learning.report()['reviewed_count'],0)
        journal.save_journal({'id':item['id'],'review_keep':'平仓后确认严格执行止损'})
        self.assertEqual(learning.report()['reviewed_count'],1)

    def test_filters_sample_provenance_and_no_pnl_inference(self):
        self.record('a',reflection='做了复盘',review_mistakes='stop,size')
        self.record('b','okx',closed=NOW-60*DAY,opened=NOW-61*DAY,reflection='较早的复盘',review_mistakes='stop')
        self.record('c','bybit',reflection='亏损复盘',review_keep='按原计划退出')
        self.record('d','variational',closed=None,reflection='手动已平仓',review_mistakes='chasing')
        self.record('e',closed=NOW+DAY,reflection='未来关闭时间不能统计',review_mistakes='chasing')
        a=learning.report()
        self.assertEqual((a['closed_count'],a['structured_count'],a['unknown_close_count']),(2,2,1))
        self.assertEqual({p['key']:p['count'] for p in a['patterns']},{'size':1,'stop':1})
        b=learning.report(90,'okx')
        self.assertEqual((b['closed_count'],b['patterns'][0]['count']),(1,1))
        self.assertEqual(b['patterns'][0]['journals'][0]['id'],'b')
        self.assertEqual(learning.report(30,'bybit')['patterns'],[])
        self.assertEqual(learning.report(30,'variational')['closed_count'],0)
        with self.assertRaises(ValueError):learning.report(31)
        with self.assertRaises(ValueError):learning.report(30,'unknown')

    def test_pin_saved_text_is_idempotent_and_does_not_rewrite_history(self):
        self.record(review_next='先写止损再算数量')
        a=learning.create_action({'journal_id':'source','instruction':'不能覆盖服务端笔记'})
        self.assertEqual(a['instruction'],'先写止损再算数量')
        self.assertEqual(learning.create_action({'journal_id':'source'})['id'],a['id'])
        learning.archive_action({'id':a['id'],'archived':True})
        self.assertEqual(learning.create_action({'journal_id':'source'})['id'],a['id'])
        journal.save_journal({'id':'source','review_next':'新的改进'})
        b=learning.create_action({'journal_id':'source'})
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(len(learning.report()['actions']),2)
        self.assertEqual({x['instruction'] for x in learning.report()['actions']},{'新的改进','先写止损再算数量'})

    def test_practice_requires_later_closed_trade_and_supports_undo(self):
        self.record(review_next='不扩大止损')
        a=learning.create_action({'journal_id':'source'})
        self.record('old',opened=NOW-1,closed=NOW+2)
        self.record('open',opened=NOW+1,closed=None,status_override='test')
        with journal.connect() as db:
            raw=journal.all_journals(db)
            item=next(j for j in raw if j['id']=='open');item['status']='持仓中'
            db.execute('UPDATE journals SET data=? WHERE id=?',(json.dumps(item),'open'))
        self.record('new','okx',opened=NOW+1,closed=NOW+2)
        payload=dict(action_id=a['id'],journal_id='new',outcome='followed',note='实际按最初止损退出')
        with self.assertRaises(ValueError):learning.practice(payload)
        with patch.object(journal,'now_ms',return_value=NOW+3):
            for jid in ('source','old','open'):
                with self.assertRaises(ValueError):learning.practice(dict(payload,journal_id=jid))
            with self.assertRaises(ValueError):learning.practice(dict(payload,note=' '))
            p=learning.practice(payload)
            self.assertEqual(learning.practice(payload)['id'],p['id'])
            with self.assertRaises(ValueError):learning.practice(dict(payload,outcome='missed'))
            report=learning.report(30,'binance')
            self.assertEqual(report['actions'][0]['practices'][0]['symbol'],'ETHUSDT')
            self.assertNotIn('new',[j['id'] for j in report['actions'][0]['eligible']])
            learning.undo_practice({'id':p['id']})
            new=learning.practice(dict(payload,outcome='missed',note='后来还是放宽了止损'))
            self.assertNotEqual(p['id'],new['id'])
            learning.archive_action({'id':a['id'],'archived':True})
            with self.assertRaises(ValueError):learning.practice(payload)
            with journal.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM review_practices').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT SUM(voided) FROM review_practices').fetchone()[0],1)

    def test_manual_entry_timezone_and_impossible_close_are_checked(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        base={'id':'manual','status':'已平仓','opened_at':datetime.fromtimestamp(NOW/1000,ZoneInfo('Asia/Hong_Kong')).replace(tzinfo=None).isoformat()}
        self.assertEqual(learning.opened(base),NOW)
        a={'journal_id':'source','created':NOW-1}
        self.assertTrue(learning.practice_eligible(base,a,NOW+1))
        for bad in ({'closed_ms':True},{'closed_ms':NOW-1},{'closed_ms':NOW+100}):
            self.assertFalse(learning.practice_eligible(dict(base,execution=bad),a,NOW+1))

    def test_optimistic_conflict_keeps_new_fields_and_noop_preserves_timestamp(self):
        j=journal.save_journal({'symbol':'ETHUSDT','review_next':'下单前计算仓位'})
        with patch.object(journal,'now_ms',return_value=NOW+10):
            same=journal.save_journal({'id':j['id'],'review_next':j['review_next']})
            self.assertEqual(same['updated'],j['updated'])
            journal.save_journal({'id':j['id'],'review_next':'只开计划内仓位','base_values':{'review_next':j['review_next']}})
            with self.assertRaises(journal.JournalConflict):
                journal.save_journal({'id':j['id'],'review_next':'旧浏览器改动','base_values':{'review_next':j['review_next']}})
