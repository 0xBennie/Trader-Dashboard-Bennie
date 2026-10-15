import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import journal
import recorder
import training


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = journal.DB
        journal.DB = Path(self.temp.name) / 'test.sqlite3'
        self.stamp = 2000000000000
        self.clock = patch.object(training, 'now_ms', side_effect=lambda: self.stamp)
        self.clock.start()
        journal.init()
        training.init()

    def tearDown(self):
        self.clock.stop()
        journal.DB = self.old_db
        self.temp.cleanup()

    def policy(self, **kwargs):
        p = dict(version=training.get_policy()['version'], confirmed=True, max_trade_risk_usdt='25',
                 max_daily_loss_usdt='100', max_entries_per_day=5, cooldown_seconds=60)
        p.update(kwargs)
        return training.save_policy(p)

    def intent(self, **kwargs):
        data = dict(version=0, symbol='ETHUSDT', direction='LONG', entry_price='100', quantity='2',
                    stop_loss='90', take_profit='120', thesis='收盘确认区间收复', invalidation='跌破90', emotion='calm')
        data.update(kwargs)
        return training.save_intent(data)

    def evidence(self, **kwargs):
        result = dict(captured_at=self.stamp, quote=dict(symbol='ETHUSDT', price='100', captured_at=self.stamp),
                      positions=dict(captured_at=self.stamp, rows=[]), orders=dict(captured_at=self.stamp, rows=[], complete=True),
                      ledger=dict(captured_at=self.stamp, complete=True, day_start=training.day_bounds(self.stamp)[1], through=self.stamp, order_count=0),
                      account=dict(captured_at=self.stamp, complete=True, daily_realized_pnl_usdt='0', day_start=training.day_bounds(self.stamp)[1]), errors=[])
        result.update(kwargs)
        return result

    def ai(self, **kwargs):
        data = dict(verdict='support', summary='计划证据完整', supporting=['新鲜行情与明确止损'], opposing=['存在跳空风险'],
                    unknowns=['未来价格路径'], invalidation='跌破90', next_step='本人核对并决定')
        data.update(kwargs)
        return data

    def reviewed(self, item=None):
        item = item or self.intent()
        item = training.begin_review(item['id'], item['version'], self.evidence())
        self.assertEqual(item['status'], 'reviewing', item['blockers'])
        return training.complete_review(item['id'], item['review_token'], self.ai(), self.evidence())

    def permitted(self, item=None, key=None):
        item = self.reviewed(item)
        return training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key=key or 'confirm-' + item['id']), self.evidence())

    def put_fills(self, fills, position='0', ps='LONG', reconcile=True):
        with journal.connect() as db:
            for fill in fills:
                db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?)', (fill['symbol'], str(fill['id']), fill['time'], json.dumps(fill)))
            if reconcile:
                positions = [dict(symbol='ETHUSDT', positionSide=ps, positionAmt=str(position))] if str(position) != '0' else []
                recorder.reconcile(db, positions, self.stamp, queue_ai=False)
            return training.reconcile_audit(db, self.stamp)

    def fill(self, fid=1, order=1, qty='2', side='BUY', price='100', when=None, ps='LONG', **kwargs):
        result = dict(id=fid, orderId=order, symbol='ETHUSDT', positionSide=ps, qty=qty, side=side, price=price,
                      time=self.stamp if when is None else when, realizedPnl='0', commission='0.1', commissionAsset='USDT')
        result.update(kwargs)
        return result

    def test_policy_requires_explicit_confirmation_and_amounts(self):
        self.assertFalse(training.get_policy()['configured'])
        self.assertIsNone(training.get_policy()['max_trade_risk_usdt'])
        for data in ({'version': 0, 'confirmed': True}, {'version': 0, 'confirmed': False}):
            with self.assertRaises(ValueError):
                training.save_policy(data)
        p = self.policy()
        self.assertEqual(p['enabled_at'], self.stamp)
        self.stamp += 100
        p = self.policy()
        self.assertEqual(p['enabled_at'], self.stamp - 100)
        self.assertEqual(p['version'], 2)
        with self.assertRaises(training.Conflict):
            self.policy(version=1)

    def test_invalid_policy_numbers_and_partial_draft_validation(self):
        for amount in ('NaN', 'Infinity', '-1', '0', '1e200'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.policy(max_trade_risk_usdt=amount)
        draft = training.save_intent({'version': 0, 'thesis': ''})
        self.assertEqual(draft['status'], 'draft')
        self.assertFalse(draft['risk']['valid'])
        for image in ('https://external.test/a.png', '/assets/../secret', '//external.test/a.png'):
            with self.assertRaises(ValueError):
                self.intent(image=image)

    def test_missing_policy_reason_and_stop_never_start_ai(self):
        item = self.intent(thesis='', stop_loss='')
        item = training.begin_review(item['id'], item['version'], self.evidence())
        self.assertEqual(item['status'], 'needs_info')
        self.assertIsNone(item['review_token'])
        self.assertTrue({'policy_unconfigured', 'missing_thesis', 'invalid_stop_risk'} <= {b['code'] for b in item['blockers']})

    def test_long_short_nominal_risk_and_wrong_side(self):
        self.policy()
        self.assertEqual(self.intent()['risk']['risk_usdt'], '20')
        short = self.intent(direction='SHORT', stop_loss='110', take_profit='80')
        self.assertEqual(short['risk']['risk_usdt'], '20')
        self.assertEqual(short['risk']['rr'], '2')
        bad = self.intent(stop_loss='110')
        self.assertFalse(bad['risk']['valid'])
        bad = training.begin_review(bad['id'], bad['version'], self.evidence())
        self.assertEqual(bad['status'], 'needs_info')

    def test_complete_review_and_idempotent_confirmation(self):
        self.policy()
        item = self.reviewed()
        self.assertEqual(item['status'], 'awaiting_confirmation')
        payload = dict(version=item['version'], idempotency_key='stable-confirmation')
        result = training.confirm_intent(item['id'], payload, self.evidence())
        repeated = training.confirm_intent(item['id'], payload, {})
        self.assertEqual(result['status'], 'permitted')
        self.assertEqual(result['permit']['id'], repeated['permit']['id'])
        with self.assertRaises(training.Conflict):
            training.confirm_intent(item['id'], dict(version=result['version'], idempotency_key='different-confirmation'), self.evidence())
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM training_permits').fetchone()[0], 1)
        self.assertTrue(training.today()['training_complete'])

    def test_same_idempotency_key_cannot_confirm_another_intent(self):
        self.policy()
        self.permitted(key='unique-confirmation')
        other = self.reviewed()
        with self.assertRaises(training.Conflict):
            training.confirm_intent(other['id'], dict(version=other['version'], idempotency_key='unique-confirmation'), self.evidence())

    def test_failed_or_malformed_ai_never_approves(self):
        self.policy()
        for ai in (None, {'error': 'timeout'}, self.ai(supporting=[]), self.ai(opposing='none'), self.ai(verdict='approve')):
            item = self.intent()
            started = training.begin_review(item['id'], item['version'], self.evidence())
            result = training.complete_review(item['id'], started['review_token'], ai, self.evidence())
            self.assertEqual(result['status'], 'review_failed')
            with self.assertRaises(training.Conflict):
                training.confirm_intent(item['id'], dict(version=result['version'], idempotency_key='failed-' + item['id']), self.evidence())

    def test_ai_reject_and_needs_info_are_structured(self):
        self.policy()
        for verdict, status in (('reject', 'rejected'), ('needs_info', 'needs_info')):
            item = self.intent()
            item = training.begin_review(item['id'], item['version'], self.evidence())
            item = training.complete_review(item['id'], item['review_token'], self.ai(verdict=verdict), self.evidence())
            self.assertEqual(item['status'], status)

    def test_old_ai_cannot_overwrite_modified_plan_or_policy(self):
        self.policy()
        for change_policy in (False, True):
            item = self.intent()
            item = training.begin_review(item['id'], item['version'], self.evidence())
            if change_policy:
                self.policy()
            else:
                training.save_intent(dict(id=item['id'], version=item['version'], quantity='1'))
            with self.assertRaises(training.Conflict):
                training.complete_review(item['id'], item['review_token'], self.ai(), self.evidence())
            self.assertNotEqual(training.get_intent(item['id'])['status'], 'awaiting_confirmation')

    def test_restart_marks_inflight_review_failed(self):
        self.policy()
        item = self.intent()
        item = training.begin_review(item['id'], item['version'], self.evidence())
        training.init()
        self.assertEqual(training.get_intent(item['id'])['status'], 'review_failed')
        with self.assertRaises(training.Conflict):
            training.complete_review(item['id'], item['review_token'], self.ai(), self.evidence())

    def test_each_evidence_source_is_required_fresh_complete_and_not_future(self):
        self.policy()
        cases = []
        for key in ('quote', 'positions', 'orders', 'account', 'ledger'):
            e = self.evidence()
            del e[key]
            cases.append(e)
            e = self.evidence()
            e[key]['captured_at'] = self.stamp - 91000
            cases.append(e)
            e = self.evidence()
            e[key]['captured_at'] = self.stamp + 1
            cases.append(e)
        e = self.evidence(); del e['account']['daily_realized_pnl_usdt']; cases.append(e)
        e = self.evidence(); e['account']['complete'] = False; cases.append(e)
        e = self.evidence(); e['account']['day_start'] -= 86400000; cases.append(e)
        e = self.evidence(); e['orders']['complete'] = False; cases.append(e)
        e = self.evidence(); e['ledger']['complete'] = False; cases.append(e)
        e = self.evidence(); e['ledger']['day_start'] -= 86400000; cases.append(e)
        e = self.evidence(); e['ledger']['through'] -= 61000; cases.append(e)
        e = self.evidence(); e['ledger']['through'] += 1; cases.append(e)
        for e in cases:
            item = self.intent()
            item = training.begin_review(item['id'], item['version'], e)
            self.assertEqual(item['status'], 'needs_info', e)

    def test_ai_completion_and_confirm_recheck_fresh_quote(self):
        self.policy()
        item = self.intent()
        started = training.begin_review(item['id'], item['version'], self.evidence())
        stale = self.evidence()
        self.stamp += 31000
        result = training.complete_review(item['id'], started['review_token'], self.ai(), stale)
        self.assertEqual(result['status'], 'needs_info')
        item = self.reviewed()
        stale = self.evidence()
        self.stamp += 31000
        result = training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key='stale-confirmation'), stale)
        self.assertEqual(result['status'], 'needs_info')
        self.assertIsNone(result['permit'])

    def test_fomo_cooldown_persists_restart_refresh_and_switch_to_calm(self):
        self.policy()
        item = self.intent(emotion='fomo')
        deadline = item['cooldown_until']
        item = training.save_intent(dict(id=item['id'], version=item['version'], emotion='calm'))
        self.assertEqual(item['cooldown_until'], deadline)
        item = self.reviewed(item)
        self.assertEqual(item['status'], 'cooldown')
        training.init()
        item = training.get_intent(item['id'])
        result = training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key='cooldown-confirm'), self.evidence())
        self.assertEqual(result['status'], 'cooldown')
        self.stamp = deadline + 1
        ready = training.get_intent(item['id'])
        self.assertEqual(ready['status'], 'awaiting_confirmation')
        self.assertIsNone(ready['permit'])
        # A new FOMO episode on the old draft starts a new deadline.
        again = training.save_intent(dict(id=ready['id'], version=ready['version'], emotion='fomo'))
        self.assertEqual(again['cooldown_until'], self.stamp + 60000)

    def test_new_calm_plan_cannot_bypass_another_plan_cooldown(self):
        self.policy()
        emotional = self.intent(emotion='revenge')
        calm = self.reviewed()
        self.assertEqual(calm['status'], 'cooldown')
        self.assertEqual(calm['cooldown_until'], emotional['cooldown_until'])

    def test_daily_entry_limit_reserves_unconsumed_permits(self):
        self.policy(max_entries_per_day=1)
        one = self.reviewed()
        two = self.reviewed()
        first = training.confirm_intent(one['id'], dict(version=one['version'], idempotency_key='first-confirmation'), self.evidence())
        second = training.confirm_intent(two['id'], dict(version=two['version'], idempotency_key='second-confirmation'), self.evidence())
        self.assertEqual(first['status'], 'permitted')
        self.assertEqual(second['status'], 'needs_info')
        self.assertIn('entry_limit', {b['code'] for b in second['blockers']})

    def test_daily_risk_reserves_worst_price_risk_and_counts_realized_loss(self):
        self.policy(max_daily_loss_usdt='30')
        first = self.reviewed()
        second = self.reviewed()
        self.assertEqual(training.confirm_intent(first['id'], dict(version=first['version'], idempotency_key='risk-confirm-one'), self.evidence())['status'], 'permitted')
        result = training.confirm_intent(second['id'], dict(version=second['version'], idempotency_key='risk-confirm-two'), self.evidence())
        self.assertIn('daily_risk_exceeded', {b['code'] for b in result['blockers']})
        self.policy(max_daily_loss_usdt='100')
        item = self.intent()
        e = self.evidence(); e['account']['daily_realized_pnl_usdt'] = '-85'
        item = training.begin_review(item['id'], item['version'], e)
        self.assertIn('daily_risk_exceeded', {b['code'] for b in item['blockers']})

    def test_price_tolerance_and_single_risk_limit_cannot_be_overruled_by_ai(self):
        self.policy()
        item = self.intent(quantity='3')
        item = training.begin_review(item['id'], item['version'], self.evidence())
        self.assertIn('trade_risk_exceeded', {b['code'] for b in item['blockers']})
        item = self.reviewed()
        e = self.evidence(); e['quote']['price'] = '101'
        item = training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key='moved-price-confirm'), e)
        self.assertIn('price_moved', {b['code'] for b in item['blockers']})

    def test_policy_change_revokes_permit_and_idempotent_retry_does_not_reactivate(self):
        self.policy()
        item = self.permitted(key='policy-change-confirm')
        self.stamp += 100
        self.policy()
        updated = training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key='policy-change-confirm'), {})
        self.assertEqual(updated['status'], 'invalidated')
        self.assertEqual(updated['permit']['revoked_at'], self.stamp)

    def test_expired_permit_is_not_reactivated_on_retry(self):
        self.policy(permit_seconds=1)
        item = self.permitted(key='expires-confirm')
        self.stamp += 1001
        result = training.confirm_intent(item['id'], dict(version=item['version'], idempotency_key='expires-confirm'), {})
        self.assertEqual(result['status'], 'expired')
        self.assertEqual(result['permit']['id'], item['permit']['id'])

    def test_real_order_match_groups_fills_and_consumes_once(self):
        self.policy()
        item = self.permitted()
        self.stamp += 100
        audits = self.put_fills([self.fill(qty='1'), self.fill(fid=2, qty='1', when=self.stamp + 10)], position='2')
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]['status'], 'matched')
        self.assertEqual(training.get_intent(item['id'])['status'], 'matched')
        self.stamp += 100
        audits = self.put_fills([self.fill(fid=3, order=2)], position='4')
        self.assertEqual(sum(a['status'] == 'matched' for a in audits), 1)
        self.assertEqual(sum(a['status'] == 'unreviewed' for a in audits), 1)
        metrics = training.today()['metrics']
        self.assertEqual(metrics['new_risk_orders'], 2)
        self.assertEqual(metrics['pretrade_coverage']['ratio'], '0.5')

    def test_external_trade_without_plan_is_unreviewed_and_cannot_be_backfilled(self):
        self.policy()
        self.stamp += 100
        audits = self.put_fills([self.fill()], position='2')
        self.assertEqual(audits[0]['status'], 'unreviewed')
        self.assertEqual(audits[0]['status_label'], '违规交易')
        self.stamp += 100
        self.permitted()
        self.assertEqual(training.today()['audits'][0]['status'], 'unreviewed')

    def test_history_before_activation_is_never_a_violation(self):
        self.put_fills([self.fill(when=self.stamp - 100)], position='2')
        self.policy()
        state = training.today()
        self.assertEqual(state['audits'][0]['status'], 'historical')
        self.assertEqual(state['audits'][0]['status_label'], '历史未留预审')
        self.assertEqual(state['metrics']['new_risk_orders'], 0)
        self.assertIsNone(state['metrics']['pretrade_coverage']['ratio'])

    def test_partial_quantity_and_ambiguous_candidates_stay_pending(self):
        self.policy()
        self.permitted()
        self.stamp += 10
        result = self.put_fills([self.fill(qty='1')], position='1')
        self.assertEqual(result[0]['status'], 'pending_match')
        self.assertEqual(result[0]['status_label'], '待核对')
        self.stamp += 10
        result = self.put_fills([self.fill(fid=2, qty='1')], position='2')
        self.assertEqual(result[0]['status'], 'matched')
        # A later fill outside the confirmed quantity removes false coverage.
        result = self.put_fills([self.fill(fid=3, qty='1')], position='3')
        self.assertEqual(result[0]['status'], 'unreviewed')
        self.assertEqual(training.today()['metrics']['matched_orders'], 0)

    def test_two_identical_permits_are_not_matched_by_time_proximity(self):
        self.policy()
        self.permitted()
        self.permitted()
        self.stamp += 100
        audit = self.put_fills([self.fill()], position='2')[0]
        self.assertEqual(audit['status'], 'pending_match')
        self.assertIsNone(audit['permit_id'])

    def test_all_fill_prices_and_times_must_match_not_only_average(self):
        self.policy()
        self.permitted()
        self.stamp += 100
        audits = self.put_fills([self.fill(qty='1', price='99'), self.fill(fid=2, qty='1', price='101')], position='2')
        self.assertEqual(audits[0]['price'], '100')
        self.assertEqual(audits[0]['status'], 'unreviewed')

    def test_same_millisecond_and_expired_trade_cannot_match(self):
        self.policy(permit_seconds=1)
        item = self.permitted()
        self.assertEqual(self.put_fills([self.fill()], position='2')[0]['status'], 'unreviewed')
        self.stamp = item['permit']['expires_at']
        audits = self.put_fills([self.fill(fid=2, order=2)], position='4')
        self.assertTrue(all(a['status'] == 'unreviewed' for a in audits))

    def test_reduce_and_close_orders_are_excluded_but_reversal_risk_is_included(self):
        self.policy()
        self.stamp += 10
        self.put_fills([self.fill(qty='2', ps='BOTH')], position='2', ps='BOTH')
        self.stamp += 10
        self.put_fills([self.fill(fid=2, order=2, qty='1', side='SELL', ps='BOTH')], position='1', ps='BOTH')
        self.stamp += 10
        audits = self.put_fills([self.fill(fid=3, order=3, qty='3', side='SELL', ps='BOTH')], position='-2', ps='BOTH')
        self.assertEqual(len(audits), 2)
        reverse = next(a for a in audits if a['order_key'] == '3')
        self.assertEqual(reverse['quantity'], '2')
        self.assertEqual(reverse['direction'], 'SHORT')

    def test_unaligned_one_way_order_is_unknown_not_falsely_approved(self):
        self.policy()
        self.stamp += 10
        audits = self.put_fills([self.fill(ps='BOTH')], reconcile=False)
        self.assertEqual(audits[0]['status'], 'pending_match')
        self.assertEqual(audits[0]['risk_classification'], 'unknown')
        self.assertEqual(training.today()['metrics']['new_risk_orders'], 0)
        item = self.intent()
        item = training.begin_review(item['id'], item['version'], self.evidence())
        self.assertIn('risk_orders_unresolved', {b['code'] for b in item['blockers']})

    def test_removed_experiment_cannot_block_a_new_plan_or_reuse_old_approval(self):
        self.policy()
        item = self.intent(strategy_id='support_reclaim', strategy_version='1.0.0', backtest_id='old-run', rules_confirmed=True)
        self.assertEqual(item['strategy_id'], '')
        self.assertEqual(item['backtest_id'], '')
        self.assertFalse(item['rules_confirmed'])
        item = self.reviewed(item)
        self.assertEqual(item['status'], 'awaiting_confirmation')
        # Old persisted versions still carrying an experiment cannot bypass re-review.
        with journal.connect() as db:
            legacy = dict(item, strategy_id='support_reclaim', backtest_id='old-run')
            problems = training._hard_checks(db, legacy, self.evidence(), self.stamp)
        self.assertIn('experiment_retired', {b['code'] for b in problems})
        saved = training.save_intent(dict(item, thesis='更新依据'))
        self.assertIsNone(saved['review'])
        self.assertIsNone(saved['permit'])
        self.assertEqual(saved['status'], 'draft')

    def test_observation_and_weekly_task_complete_without_any_trades(self):
        state = training.save_activity({'kind': 'observe', 'reason': '证据不足，今天观望'})
        self.assertTrue(state['training_complete'])
        state = training.save_activity({'kind': 'observe', 'reason': '保持观望'})
        self.assertEqual(len(state['activities']), 1)
        state = training.save_activity({'kind': 'weekly_task', 'title': '先写失效条件'})
        task = state['tasks'][0]
        with self.assertRaises(training.Conflict):
            training.save_activity({'kind': 'weekly_task', 'title': '不要追单'})
        state = training.save_activity({'kind': 'complete_task', 'task_id': task['id']})
        repeated = training.save_activity({'kind': 'complete_task', 'task_id': task['id']})
        self.assertEqual(len(state['activities']), len(repeated['activities']))
        self.assertEqual(state['tasks'][0]['status'], 'complete')
        self.assertEqual(state['metrics']['new_risk_orders'], 0)
        self.assertIsNone(state['metrics']['review_completion']['ratio'])

    def test_performance_and_review_metrics_use_closed_cycles_and_fee_assets(self):
        self.policy()
        self.permitted()
        self.stamp += 10
        self.put_fills([self.fill()], position='2')
        self.stamp += 10
        self.put_fills([self.fill(fid=2, order=2, side='SELL', price='110', realizedPnl='20')])
        state = training.today()
        self.assertEqual(state['metrics']['performance']['planned']['closed_cycles'], 1)
        self.assertEqual(state['metrics']['performance']['planned']['net_usdt'], '19.8')
        self.assertEqual(state['metrics']['review_completion']['denominator'], 1)
        self.assertEqual(state['metrics']['review_completion']['numerator'], 0)
        with journal.connect() as db:
            record = next(j for j in journal.all_journals(db) if j.get('execution'))
        with patch.object(journal, 'now_ms', return_value=self.stamp):
            journal.save_journal({'id': record['id'], 'reflection': '遵守原计划'})
        self.assertEqual(training.today()['metrics']['review_completion']['ratio'], '1')
        self.assertIsNone(training.today()['metrics']['plan_adherence']['ratio'])

    def test_opening_ai_report_and_old_reflection_do_not_count_as_post_close_review(self):
        self.policy()
        self.stamp += 10
        self.put_fills([self.fill()], position='2')
        with journal.connect() as db:
            record = next(j for j in journal.all_journals(db) if j.get('execution'))
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)', ('opening-report', record['id'], self.stamp, 'complete', '{}'))
        with patch.object(journal, 'now_ms', return_value=self.stamp):
            journal.save_journal({'id': record['id'], 'reflection': '开仓时的想法'})
        self.stamp += 10
        self.put_fills([self.fill(fid=2, order=2, side='SELL')])
        self.assertEqual(training.today()['metrics']['review_completion']['numerator'], 0)
        with journal.connect() as db:
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)', ('closing-report', record['id'], self.stamp, 'complete', '{}'))
        self.assertEqual(training.today()['metrics']['review_completion']['numerator'], 1)
        self.assertEqual(training.today()['metrics']['personal_review_completion']['numerator'], 0)
        self.assertEqual(training.today()['metrics']['ai_report_coverage']['numerator'], 1)

    def test_entry_parameter_metric_identifies_preexisting_plan_price_deviation(self):
        self.policy()
        intent = self.permitted()
        self.stamp += 10
        self.put_fills([self.fill(price='101')], position='2')
        state = training.today()
        audit = state['audits'][0]
        self.assertEqual(audit['status'], 'unreviewed')
        self.assertEqual(audit['candidate_intent_ids'], [intent['id']])
        self.assertEqual(audit['deviation_fields'], ['price'])
        self.assertEqual(state['metrics']['entry_parameter_adherence']['ratio'], '0')
        self.assertIsNone(state['metrics']['plan_adherence']['ratio'])

    def test_delayed_fill_match_cannot_be_overwritten_by_review_of_expired_permit(self):
        self.policy(permit_seconds=1)
        intent = self.permitted()
        issued = self.stamp
        self.stamp += 1100
        expired = training.get_intent(intent['id'])
        with journal.connect() as db:
            fill = self.fill(when=issued + 100)
            db.execute('INSERT INTO trades VALUES(?,?,?,?)', (fill['symbol'], str(fill['id']), fill['time'], json.dumps(fill)))
            recorder.reconcile(db, [dict(symbol='ETHUSDT', positionSide='LONG', positionAmt='2')], self.stamp, queue_ai=False)
        with self.assertRaises(training.Conflict):
            training.begin_review(intent['id'], expired['version'], self.evidence())
        self.assertEqual(training.today()['audits'][0]['status'], 'matched')
        self.assertEqual(training.get_intent(intent['id'])['status'], 'matched')

    def test_cancelled_permit_cannot_match_a_later_order(self):
        self.policy()
        item = self.permitted()
        self.stamp += 10
        cancelled = training.cancel_intent(item['id'], {'version': item['version']})
        self.assertEqual(cancelled['status'], 'cancelled')
        self.stamp += 10
        self.assertEqual(self.put_fills([self.fill()], position='2')[0]['status'], 'unreviewed')

    def material_evidence(self):
        evidence = self.evidence()
        evidence['positions']['rows'] = [dict(symbol='ETHUSDT', positionSide='LONG', positionAmt='1', entryPrice='99', markPrice='100', unRealizedProfit='1')]
        evidence['orders']['rows'] = [dict(symbol='ETHUSDT', positionSide='LONG', side='SELL', type='STOP_MARKET', price='0', quantity='1',
                                          triggerPrice='95', reduceOnly=True, closePosition=False, status='NEW', workingType='MARK_PRICE', priceProtect=True)]
        return evidence

    def test_initial_ai_evidence_remains_immutable_through_completion_and_confirmation(self):
        self.policy()
        item = self.intent()
        original = self.material_evidence()
        item = training.begin_review(item['id'], item['version'], original)
        input_stamp = self.stamp
        original['positions']['rows'][0]['positionAmt'] = '999'
        self.assertEqual(item['review']['input_evidence']['positions']['rows'][0]['positionAmt'], '1')
        self.stamp += 10
        current = self.material_evidence()
        current['quote']['price'] = '100.1'
        current['positions']['rows'][0].update(markPrice='100.1', unRealizedProfit='1.1')
        completed = training.complete_review(item['id'], item['review_token'], self.ai(), current)
        self.assertEqual(completed['status'], 'awaiting_confirmation')
        self.assertEqual(completed['review']['input_evidence']['captured_at'], input_stamp)
        self.assertEqual(completed['review']['evidence'], completed['review']['input_evidence'])
        self.assertEqual(completed['review']['completion_evidence']['captured_at'], self.stamp)
        self.assertIsNone(completed['review']['confirmation_evidence'])
        completion_stamp = self.stamp
        self.stamp += 10
        confirmed = training.confirm_intent(item['id'], {'version': completed['version'], 'idempotency_key': 'separate-evidence'}, self.material_evidence())
        self.assertEqual(confirmed['status'], 'permitted')
        self.assertEqual(confirmed['review']['input_evidence']['captured_at'], input_stamp)
        self.assertEqual(confirmed['review']['completion_evidence']['captured_at'], completion_stamp)
        self.assertEqual(confirmed['review']['confirmation_evidence']['captured_at'], self.stamp)

    def test_material_position_changes_during_ai_require_fresh_review(self):
        self.policy()
        for field, value in [('symbol', 'BTCUSDT'), ('positionSide', 'SHORT'), ('positionAmt', '2'), ('entryPrice', '98')]:
            with self.subTest(field=field):
                item = self.intent()
                item = training.begin_review(item['id'], item['version'], self.material_evidence())
                changed = self.material_evidence()
                changed['positions']['rows'][0][field] = value
                result = training.complete_review(item['id'], item['review_token'], self.ai(), changed)
                self.assertEqual(result['status'], 'needs_info')
                self.assertTrue(result['review']['requires_review'])
                self.assertIn('positions_changed', {b['code'] for b in result['blockers']})

    def test_material_order_changes_during_ai_require_fresh_review(self):
        self.policy()
        for field, value in [('price', '94'), ('quantity', '2'), ('triggerPrice', '94'), ('reduceOnly', False), ('closePosition', True),
                             ('status', 'PARTIALLY_FILLED'), ('workingType', 'CONTRACT_PRICE'), ('priceProtect', False)]:
            with self.subTest(field=field):
                item = self.intent()
                item = training.begin_review(item['id'], item['version'], self.material_evidence())
                changed = self.material_evidence()
                changed['orders']['rows'][0][field] = value
                result = training.complete_review(item['id'], item['review_token'], self.ai(), changed)
                self.assertEqual(result['status'], 'needs_info')
                self.assertIn('orders_changed', {b['code'] for b in result['blockers']})

    def test_material_changes_while_waiting_for_confirmation_block_even_if_still_within_limits(self):
        self.policy()
        for section in ('positions', 'orders', 'account'):
            with self.subTest(section=section):
                item = self.intent()
                item = training.begin_review(item['id'], item['version'], self.material_evidence())
                item = training.complete_review(item['id'], item['review_token'], self.ai(), self.material_evidence())
                changed = self.material_evidence()
                if section == 'account':
                    changed[section]['daily_realized_pnl_usdt'] = '0.01'
                else:
                    changed[section]['rows'] = []
                result = training.confirm_intent(item['id'], {'version': item['version'], 'idempotency_key': 'changed-' + section}, changed)
                self.assertEqual(result['status'], 'needs_info')
                self.assertIsNone(result['permit'])
                self.assertIn(section + '_changed', {b['code'] for b in result['blockers']})
                self.assertEqual(result['review']['confirmation_evidence'], changed)
                # Returning to the original exposure does not resurrect the old approval.
                with self.assertRaises(training.Conflict):
                    training.confirm_intent(item['id'], {'version': result['version'], 'idempotency_key': 'restore-' + section}, self.material_evidence())

    def test_account_realized_pnl_change_during_ai_requires_reassessment(self):
        self.policy()
        item = self.intent()
        item = training.begin_review(item['id'], item['version'], self.evidence())
        changed = self.evidence()
        changed['account']['daily_realized_pnl_usdt'] = '-0.01'
        item = training.complete_review(item['id'], item['review_token'], self.ai(), changed)
        self.assertEqual(item['status'], 'needs_info')
        self.assertIn('account_changed', {b['code'] for b in item['blockers']})

    def test_decimal_format_row_order_and_volatile_marks_are_not_material_changes(self):
        self.policy()
        item = self.intent()
        original = self.material_evidence()
        original['positions']['rows'].append(dict(symbol='BTCUSDT', positionSide='SHORT', positionAmt='-1', entryPrice='100'))
        original['orders']['rows'].append(dict(symbol='ETHUSDT', positionSide='LONG', side='SELL', type='TAKE_PROFIT_MARKET', quantity='1', triggerPrice='110', reduceOnly=True, status='NEW'))
        item = training.begin_review(item['id'], item['version'], original)
        self.stamp += 10
        current = json.loads(json.dumps(original))
        current['captured_at'] = self.stamp
        for key in ('quote', 'positions', 'orders', 'account', 'ledger'):
            current[key]['captured_at'] = self.stamp
        current['ledger']['through'] = self.stamp
        current['positions']['rows'][0].update(positionAmt='1.000', entryPrice='99.0000', markPrice='100.1', unRealizedProfit='1.1', updateTime=self.stamp)
        current['orders']['rows'][0].update(quantity='1.000', triggerPrice='95.0', created=self.stamp)
        current['account']['daily_realized_pnl_usdt'] = '0.000'
        current['positions']['rows'].reverse()
        current['orders']['rows'].reverse()
        completed = training.complete_review(item['id'], item['review_token'], self.ai(), current)
        self.assertEqual(completed['status'], 'awaiting_confirmation', completed['blockers'])

    def test_long_and_short_quote_and_entire_permit_must_stay_inside_stop_and_target(self):
        self.policy()
        cases = [('LONG', '99.9', '120', '99.85', 'stop'), ('SHORT', '100.1', '80', '100.15', 'stop'),
                 ('LONG', '90', '100.1', '100.15', 'target'), ('SHORT', '110', '99.9', '99.85', 'target')]
        for direction, stop, target, price, boundary in cases:
            with self.subTest(direction=direction, boundary=boundary):
                item = self.intent(direction=direction, stop_loss=stop, take_profit=target)
                current = self.evidence(); current['quote']['price'] = price
                blocked = training.begin_review(item['id'], item['version'], current)
                self.assertEqual(blocked['status'], 'needs_info')
                self.assertTrue({'quote_crossed_' + boundary, 'permit_crosses_' + boundary} <= {b['code'] for b in blocked['blockers']})
                # Even a valid current quote cannot authorize a range that crosses the boundary.
                item = self.intent(direction=direction, stop_loss=stop, take_profit=target)
                blocked = training.begin_review(item['id'], item['version'], self.evidence())
                self.assertIn('permit_crosses_' + boundary, {b['code'] for b in blocked['blockers']})

    def test_permit_boundary_touch_is_blocked_and_user_can_explicitly_narrow_tolerance(self):
        self.policy()
        for direction, stop, target in [('LONG', '99.8', '120'), ('SHORT', '100.2', '80'), ('LONG', '90', '100.2'), ('SHORT', '110', '99.8')]:
            item = self.intent(direction=direction, stop_loss=stop, take_profit=target)
            blocked = training.begin_review(item['id'], item['version'], self.evidence())
            self.assertEqual(blocked['status'], 'needs_info')
        self.policy(price_tolerance_pct='0.05')
        allowed = self.permitted(self.intent(stop_loss='99.9'))
        self.assertEqual(allowed['status'], 'permitted')
        self.assertGreater(float(allowed['permit']['price_min']), 99.9)

    def test_new_ledger_order_count_requires_review_even_if_position_and_net_pnl_unchanged(self):
        self.policy()
        item = self.reviewed()
        changed = self.evidence(); changed['ledger']['order_count'] = 1
        blocked = training.confirm_intent(item['id'], {'version': item['version'], 'idempotency_key': 'ledger-changed-key'}, changed)
        self.assertEqual(blocked['status'], 'needs_info')
        self.assertIn('ledger_changed', {b['code'] for b in blocked['blockers']})
        self.assertEqual(blocked['review']['confirmation_evidence']['ledger']['order_count'], 1)


    def test_historical_personal_reviews_work_without_risk_policy(self):
        self.put_fills([self.fill()], position='2')
        self.stamp += 10
        self.put_fills([self.fill(fid=2, order=2, side='SELL')])
        with journal.connect() as db:
            item = next(j for j in journal.all_journals(db) if j.get('execution'))
        metrics = training.today()['metrics']
        self.assertEqual(metrics['personal_review_completion']['denominator'], 1)
        self.assertEqual(metrics['personal_review_completion']['numerator'], 0)
        self.assertEqual(metrics['new_risk_orders'], 0)
        self.assertEqual(metrics['performance']['unclassified']['closed_cycles'], 0)
        self.assertEqual(metrics['review_progress']['pending'][0]['id'], item['id'])
        self.stamp += 10
        with patch.object(journal, 'now_ms', return_value=self.stamp):
            journal.save_journal({'id': item['id'], 'reflection': '平仓后确认执行偏差'})
        metrics = training.today()['metrics']
        self.assertEqual(metrics['personal_review_completion']['ratio'], '1')
        self.assertEqual(metrics['ai_report_coverage']['numerator'], 0)
        self.assertEqual(len(metrics['review_progress']['pending']), 0)
        self.assertEqual(len(metrics['review_progress']['completed']), 1)
        self.stamp += 31 * 86400000
        self.assertIsNone(training.today()['metrics']['personal_review_completion']['ratio'])

    def test_unrelated_edit_does_not_turn_preclose_observation_into_review(self):
        self.put_fills([self.fill()], position='2')
        with journal.connect() as db:
            item = next(j for j in journal.all_journals(db) if j.get('execution'))
        with patch.object(journal, 'now_ms', return_value=self.stamp):
            journal.save_journal({'id': item['id'], 'reflection': '仍在观察'})
        self.stamp += 10
        self.put_fills([self.fill(fid=2, order=2, side='SELL')])
        self.stamp += 10
        with patch.object(journal, 'now_ms', return_value=self.stamp):
            journal.save_journal({'id': item['id'], 'title': '只修改标题'})
        self.assertEqual(training.today()['metrics']['personal_review_completion']['numerator'], 0)
        with journal.connect() as db:
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?)', ('closing-report', item['id'], self.stamp, 'complete', '{}'))
        metrics = training.today()['metrics']
        self.assertEqual(metrics['personal_review_completion']['numerator'], 0)
        self.assertEqual(metrics['ai_report_coverage']['numerator'], 1)


if __name__ == '__main__':
    unittest.main()
