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
        with patch.object(market,'collect',return_value=replay), patch.object(assistant.ai_provider,'status',return_value={'available':True}), patch.object(assistant.ai_provider,'complete',return_value=({'summary':'测试'},{})) as model:
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
