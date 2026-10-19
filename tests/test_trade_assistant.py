import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import journal
import market_replay as market
import trader_memory as memory
import trade_assistant as assistant
import ai_review


NOW=1792344000000


def getter(url,params):
    frame=params.get('interval',params.get('bar','5m'))
    step=market.INTERVALS[frame]
    start=params.get('startTime',params.get('start',params.get('before',0)+1))
    end=params.get('endTime',params.get('end',params.get('after',0)-1))
    return [[t,'100','102','98','101','10',t+step-1] for t in range(start,end,step)]


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.temp.name)/'journal.sqlite3');self.dbpatch.start()
        journal.init()
        self.record=journal.save_journal(dict(exchange='variational',symbol='BTC',direction='LONG',status='持仓中',
            entry_price='100',quantity='5',stop_loss='98',take_profit='104',timeframe='5m',thesis='当时的原话'))

    def tearDown(self):
        self.dbpatch.stop();self.temp.cleanup()

    def replay(self,**updates):
        return market.collect(dict(self.record,created=NOW-3600000,**updates),NOW,getter=getter)

    def plan(self,**extra):
        return assistant.validate(dict(exchange='variational',symbol='BTC',direction='LONG',timeframe='5m',
            entry_price='100',quantity='5',stop_loss='98',take_profit='104',risk_budget='',equity='1000',
            thesis='测试理由',invalidation='收盘失守',**extra))

    def test_closed_bars_only_and_unknown_entry_is_observation(self):
        r=self.replay()
        self.assertEqual(r['anchor_kind'],'recorded_observation');self.assertIsNone(r['entry_time'])
        self.assertEqual(r['source']['exchange'],'binance');self.assertTrue(r['source']['reference_only'])
        self.assertTrue(all(c['close_time']<=NOW for w in r['windows'] for c in w['candles']))
        self.assertNotEqual(r['anchor'],NOW)

    def test_exact_entry_splits_hindsight_and_preflight_has_no_after(self):
        r=self.replay(opened_at='2026-10-19T00:00:00+08:00')
        self.assertEqual(r['anchor_kind'],'entry')
        evidence=market.for_ai(r)
        for w in evidence['windows']:
            self.assertTrue(all(c['close_time']<=w['anchor'] for c in w['before_anchor']))
            self.assertTrue(all(c['close_time']>w['anchor'] for c in w['after_anchor']))
        decision=market.collect(self.plan(),NOW,getter,preflight=True)
        self.assertTrue(all(w['after_count']==0 for w in decision['windows']))

    def test_missing_data_partial_coverage_and_unsupported_interval_stay_explicit(self):
        r=market.collect(dict(self.record,created=NOW-3600000,timeframe='20m'),NOW,lambda u,p:[])
        self.assertEqual(r['status'],'unavailable');self.assertTrue(r['interval_substitution'])
        r=market.collect(dict(self.record,created=NOW-3600000),NOW,lambda u,p:getter(u,p)[::2])
        self.assertEqual(r['status'],'partial')

    def test_replay_is_private_durable_and_ai_receives_reference_without_account_borrowing(self):
        with patch.object(journal,'now_ms',return_value=NOW+86400000):
            # Move fixture creation into the valid requested window.
            with journal.connect() as db:db.execute('UPDATE journals SET created=? WHERE id=?',(NOW,self.record['id']))
            result=market.capture(self.record['id'],getter=getter)
        self.assertEqual(market.latest(self.record['id']),result)
        _,evidence=ai_review.evidence(self.record['id'])
        self.assertTrue(evidence['market_replay']['source']['reference_only'])
        self.assertEqual(evidence['positions'],[])
        self.assertIn('personal_memory',evidence)

    def test_memory_versions_and_unknown_outcome(self):
        item=memory.save({'text':'不追涨','kind':'preference'})
        with self.assertRaises(journal.JournalConflict):memory.save({'id':item['id'],'text':'变更','version':0})
        memory.save({'id':item['id'],'text':'不追涨，等回踩','version':item['updated']})
        data=memory.snapshot()
        self.assertEqual(data['notes'][0]['history'][0]['data'],'不追涨')
        self.assertFalse(data['cases'][0]['confirmed_closed'])
        path=memory.refresh_document()
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertIn('当时的原话',path.read_text())
        self.assertIn('平仓与最终结果未核对',path.read_text())
        self.assertEqual(memory.context(exclude=self.record['id'])['related_cases'],[])

    def test_confirmed_risk_cap_cannot_be_overridden_by_larger_plan_budget(self):
        memory.save({'text':'每笔 100–200 美元','kind':'risk_rule','risk_budget_lower':'100',
                     'risk_budget_upper':'200','risk_currency':'USD'})
        plan=self.plan();plan.update(quantity='150',risk_budget='1000')
        r=assistant.evaluate(plan,market.collect(plan,NOW,getter,preflight=True),memory.context(),NOW)
        self.assertEqual(r['verdict'],'revise');self.assertEqual(r['figures']['gross_risk'],'300')
        self.assertEqual(r['figures']['effective_budget'],'200')
        self.assertTrue(any('个人记忆' in x for x in r['blockers']))
        plan['quantity']='5';plan['risk_budget']=''
        r=assistant.evaluate(plan,market.collect(plan,NOW,getter,preflight=True),memory.context(),NOW)
        self.assertEqual(r['figures']['effective_budget'],'200')
        self.assertEqual(r['figures']['risk_pct_equity'],'1.00')

    def test_candidates_are_separate_and_never_change_plan(self):
        plan=self.plan();plan.update(stop_loss='',take_profit='')
        r=assistant.evaluate(plan,market.collect(plan,NOW,getter,preflight=True),memory.context(),NOW)
        self.assertIn('stop_loss',r['candidates']);self.assertEqual(r['plan']['stop_loss'],'')
        self.assertNotIn('gross_risk',r['figures']);self.assertNotEqual(r['verdict'],'conditional')
        plan.update(stop_loss='103',take_profit='99')
        r=assistant.evaluate(plan,market.collect(plan,NOW,getter,preflight=True),memory.context(),NOW)
        self.assertEqual(r['verdict'],'revise')

    def test_public_clients_preserve_quote_product_and_confirmed_bars(self):
        source=market.instrument('bybit','BTCUSDT')
        def bybit(url,p):
            self.assertEqual(p['category'],'linear')
            return {'retCode':0,'result':{'symbol':'BTCUSDT','list':[[NOW,'100','101','99','100','5'],[NOW-300000,'100','101','99','100','5']]}}
        self.assertEqual(len(market.bars(source,'5m',NOW-300000,NOW,bybit)),1)
        source=market.instrument('okx','BTCUSDT')
        def okx(url,p):
            self.assertEqual(p['instId'],'BTC-USDT-SWAP')
            return {'code':'0','data':[[NOW-300000,'100','101','99','100','5','6','7','0']]}
        self.assertEqual(market.bars(source,'5m',NOW-300000,NOW,okx),[])

    def test_invalid_numeric_and_symbol_input_rejected(self):
        for value in ('NaN','Infinity','-1'):
            with self.assertRaises(ValueError):assistant.number(value,'价格')
        with self.assertRaises(ValueError):market.instrument('variational','BTC&url=localhost')
        with self.assertRaises(ValueError):memory.save({'text':'坏参数','kind':'risk_rule','risk_budget_lower':'200','risk_budget_upper':'100','risk_currency':'USD'})

    def test_conditions_exclude_future_oi_and_funding_and_match_btc_window(self):
        def feeds(url,p):
            if url.endswith('openInterestHist'):
                return [dict(symbol='NEARUSDT',timestamp=NOW-300000,sumOpenInterest='100'),
                        dict(symbol='NEARUSDT',timestamp=NOW,sumOpenInterest='110'),
                        dict(symbol='NEARUSDT',timestamp=NOW+1,sumOpenInterest='999')]
            if url.endswith('fundingRate'):
                return [dict(symbol='NEARUSDT',fundingTime=NOW-1,fundingRate='.0001'),
                        dict(symbol='NEARUSDT',fundingTime=NOW+1,fundingRate='.9')]
            return getter(url,p)
        plan=self.plan();plan['symbol']='NEAR'
        r=market.collect(plan,NOW,feeds,preflight=True)['conditions_before']
        self.assertEqual(r['open_interest']['change_pct'],'10.0')
        self.assertEqual(len(r['open_interest']['samples']),2)
        self.assertEqual(len(r['funding']['settled_rates']),1)
        self.assertEqual(r['relative_strength']['excess_percentage_points'],'0.00')

    def test_consultation_persists_and_model_receives_bounded_preflight_context(self):
        memory.save({'text':'原始个人规则','kind':'preference'})
        plan=self.plan();plan['use_ai']=True
        replay=market.collect(plan,NOW,getter,preflight=True)
        with patch.object(market,'collect',return_value=replay), patch.object(assistant.official_data,'binance_flow',return_value={'status':'unavailable'}), patch.object(assistant.ai_provider,'status',return_value={'available':True}), patch.object(assistant.ai_provider,'complete',return_value=({'summary':'测试'},{})) as model:
            result=assistant.create(plan)
        prompt=model.call_args.args[0]
        self.assertIn('原始个人规则',prompt)
        evidence=json.loads(prompt.split('\n',1)[1])
        self.assertTrue(all(not w['after_anchor'] for w in evidence['market']['windows']))
        self.assertEqual(assistant.listing()[0]['id'],result['id'])
        self.assertEqual(result['ai']['status'],'complete')

    def test_memory_same_millisecond_edits_keep_version_and_archive_boundary(self):
        with patch.object(journal,'now_ms',return_value=NOW):
            item=memory.save({'text':'规则'})
            updated=memory.save({'id':item['id'],'version':item['updated'],'text':'已归档','state':'archived'})
        self.assertGreater(updated['updated'],item['updated'])
        self.assertEqual(memory.context()['confirmed'],[])
        self.assertEqual(memory.snapshot()['notes'][0]['state'],'archived')


if __name__=='__main__':unittest.main()


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.temp.name)/'journal.sqlite3');self.dbpatch.start()
        journal.init()

    def tearDown(self):
        self.dbpatch.stop();self.temp.cleanup()

    def closed(self,closed_ms,realized,fee='0',**extra):
        j=journal.save_journal(dict(exchange='binance',symbol='ETHUSDT',direction='LONG',status='持仓中',entry_price='100',quantity='1'))
        with journal.connect() as db:
            d=json.loads(db.execute('SELECT data FROM journals WHERE id=?',(j['id'],)).fetchone()[0])
            d.update(status='已平仓',execution=dict(closed_ms=closed_ms,realized_pnl=realized,fees={'USDT':fee},history_complete=True),**extra)
            db.execute('UPDATE journals SET data=? WHERE id=?',(json.dumps(d),j['id']))
        return j['id']

    def test_recent_net_loss_starts_cooldown_and_old_or_winning_closes_do_not(self):
        self.closed(NOW-40*60000,'-50')
        self.closed(NOW-5*60000,'30')
        self.assertFalse(assistant.cooldown(NOW)['active'])
        jid=self.closed(NOW-10*60000,'0.10','0.30')  # gross win, net loss after fees
        c=assistant.cooldown(NOW)
        self.assertTrue(c['active']);self.assertEqual(c['trigger']['journal_id'],jid)
        self.assertEqual(c['until'],NOW-10*60000+30*60000);self.assertEqual(c['trigger']['pnl'],'-0.20')
        self.assertIn('还剩约 20 分钟',assistant.cooldown_warning(c))

    def test_variational_browser_close_uses_page_pnl(self):
        j=journal.save_journal(dict(exchange='variational',symbol='STRK',direction='LONG',status='持仓中',entry_price='1',quantity='10'))
        with journal.connect() as db:
            d=json.loads(db.execute('SELECT data FROM journals WHERE id=?',(j['id'],)).fetchone()[0])
            d.update(status='已平仓',browser_close=dict(source='authorized_browser',trade_status='confirmed',order_status='Completed',
                     reduce_only=True,quantity='10',closed_ms=NOW-60000,realized_pnl_display='-22.66',realized_pnl_asset='USDC'))
            db.execute('UPDATE journals SET data=? WHERE id=?',(json.dumps(d),j['id']))
        c=assistant.cooldown(NOW)
        self.assertTrue(c['active']);self.assertEqual(c['trigger']['asset'],'USDC')

    def test_manual_start_clear_and_minutes_setting(self):
        c=assistant.set_cooldown({'action':'start','symbol':'near'},NOW)
        self.assertTrue(c['active']);self.assertTrue(c['manual_active']);self.assertEqual(c['trigger']['symbol'],'NEAR')
        self.assertFalse(assistant.cooldown(NOW+30*60000)['active'])
        self.assertFalse(assistant.set_cooldown({'action':'clear'},NOW)['active'])
        assistant.set_cooldown({'action':'start'},NOW)
        self.assertFalse(assistant.set_cooldown({'minutes':0},NOW)['active'])
        self.assertEqual(assistant.set_cooldown({'minutes':45},NOW)['until'],NOW+45*60000)
        for bad in (-1,241,'30',True,1.5):
            with self.assertRaises(ValueError):assistant.set_cooldown({'minutes':bad},NOW)
        with self.assertRaises(ValueError):assistant.set_cooldown({'action':'erase'},NOW)

    def test_consultation_leads_with_cooldown_warning(self):
        self.closed(journal.now_ms()-60000,'-100')
        plan=assistant.validate(dict(exchange='binance',symbol='ETHUSDT',direction='LONG',timeframe='5m',entry_price='100',
            quantity='',stop_loss='',take_profit='',risk_budget='',equity='',thesis='',invalidation=''))
        replay=market.collect(plan,NOW,getter,preflight=True)
        with patch.object(market,'collect',return_value=replay), patch('official_data.binance_flow',return_value={'status':'unavailable'}):
            result=assistant.create(plan)
        self.assertTrue(result['cooldown']['active']);self.assertTrue(result['warnings'][0].startswith('冷静期：ETHUSDT 亏损平仓 -100'))


FLOW={'status':'unavailable'}


class MentorWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(journal,'DB',Path(self.temp.name)/'journal.sqlite3');self.dbpatch.start()
        journal.init()
        self.flow=patch.object(assistant.official_data,'binance_flow',return_value=FLOW);self.flow.start()

    def tearDown(self):
        self.flow.stop();self.dbpatch.stop();self.temp.cleanup()

    def plan(self,**extra):
        base=dict(exchange='variational',symbol='BTC',direction='LONG',timeframe='5m',entry_price='100',quantity='',
                  stop_loss='98',take_profit='104',risk_budget='',equity='',thesis='',invalidation='',setup_id='support_bounce')
        base.update(extra);return assistant.validate(base)

    def run_create(self,plan):
        replay=market.collect(plan,NOW,getter,preflight=True)
        with patch.object(market,'collect',return_value=replay):
            return assistant.create(plan)

    def test_preflight_uses_250_bars_and_daily_for_4h(self):
        r=market.collect(self.plan(timeframe='4h'),NOW,getter,preflight=True)
        self.assertEqual([w['interval'] for w in r['windows']],['4h','1h','1d'])
        r=market.collect(self.plan(),NOW,getter,preflight=True)
        self.assertGreaterEqual(r['windows'][0]['before_count'],249)

    def test_consultation_includes_judgment(self):
        result=self.run_create(self.plan())
        j=result['judgment']
        self.assertIn(j['state'],('go','conditional','wait_trigger','no'))
        self.assertEqual(j['source']['symbol'],'BTCUSDT');self.assertTrue(j['source']['reference_only'])
        self.assertIsInstance(j['lesson_cards'],list)
        self.assertEqual(assistant.listing()[0]['judgment']['state'],j['state'])

    def test_validate_rejects_unknown_setup_and_long_text(self):
        with self.assertRaises(ValueError):self.plan(setup_id='moon')
        with self.assertRaises(ValueError):self.plan(bet='x'*501)
        self.assertEqual(self.plan(setup_id='')['setup_id'],'')

    def open(self,**extra):
        data=dict(exchange='variational',symbol='ETH',direction='LONG',status='持仓中',entry_price='100',quantity='1',stop_loss='98')
        data.update(extra);return journal.save_journal(data)['id']

    def test_open_positions_skip_incomplete(self):
        a=self.open();self.open(stop_loss='');self.open(status='已平仓');self.open(direction='SHORT',entry_price='50',quantity='2',stop_loss='51')
        p=assistant.open_positions()
        self.assertEqual(len(p['items']),2);self.assertEqual(p['by_direction'],{'LONG':2.0,'SHORT':2.0})
        self.assertEqual(next(i['journal_id'] for i in p['items'] if i['direction']=='LONG'),a)

    def test_add_to_position_recalculates_total(self):
        jid=self.open()
        result=self.run_create(self.plan(symbol='ETH',entry_price='102',quantity='1',stop_loss='99',add_journal_id=jid))
        add=result['judgment']['add']
        self.assertEqual((add['total_qty'],add['avg_entry'],add['total_risk']),(2.0,101.0,4.0))
        self.assertIn('add_recalc',result['judgment']['lessons'])

    def test_symbol_without_binance_perp(self):
        plan=self.plan()
        replay=market.collect(plan,NOW,lambda u,p:[],preflight=True)
        with patch.object(market,'collect',return_value=replay):
            j=assistant.create(plan)['judgment']
        self.assertEqual(j['state'],'unavailable');self.assertIn('Binance',j['reason'])

    def test_judgment_failure_isolated(self):
        with patch.object(assistant.setup_judge,'judge',side_effect=RuntimeError('boom')):
            result=self.run_create(self.plan())
        self.assertEqual(result['judgment']['state'],'unavailable');self.assertIn('verdict',result)

    def test_status_summarises_system_positions_and_reminder(self):
        self.open()
        s=assistant.status()
        self.assertEqual(s['system']['version'],1);self.assertFalse(s['system']['confirmed'])
        self.assertEqual(s['open_positions']['by_direction']['LONG'],2.0)
        self.assertIn('cooldown',s);self.assertIsNone(s['reminder'])

    def test_add_over_budget_is_no_and_reports_room(self):
        jid=self.open()
        j=self.run_create(self.plan(symbol='ETH',entry_price='102',quantity='1',stop_loss='99',risk_budget='3',add_journal_id=jid))['judgment']
        self.assertEqual(j['state'],'no');self.assertTrue(any('加仓后整笔' in x for x in j['hard_fails']))
        self.assertAlmostEqual(j['add']['room_qty'],0.6666)  # (3 - 1×(100-99)) / (102-99), floored

    def test_lesson_cards_prefer_recent_repeated_mistakes(self):
        with patch('mentor_review.recent_codes',return_value={'early_exit':3}):
            j=self.run_create(self.plan())['judgment']
        self.assertIn('early_exit',[c['code'] for c in j['lesson_cards']])
