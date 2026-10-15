import json
import tempfile
import threading
import unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
import journal
from fixtures import create_journal
import recorder
from binance_client import BinanceError


def fill(id, side, qty, price='100', symbol='ABCUSDT', ps='LONG', time=None):
    return dict(id=id, orderId=1, symbol=symbol, positionSide=ps, side=side, qty=str(qty), price=price,
                time=time or (1000000+id), commission='0.01', commissionAsset='USDT', realizedPnl='0')


def pos(qty, symbol='ABCUSDT', ps='LONG'):
    return dict(symbol=symbol,positionSide=ps,positionAmt=str(qty),entryPrice='100',markPrice='101',unRealizedProfit='1')


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=journal.DB
        journal.DB=Path(self.temp.name)/'db.sqlite3'
        journal.init()

    def tearDown(self):
        journal.DB=self.old
        self.temp.cleanup()

    def put(self, fills, positions, stamp=2000000):
        with journal.connect() as db:
            for f in fills:
                db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?)',(f['symbol'],str(f['id']),f['time'],json.dumps(f)))
            return recorder.reconcile(db,positions,stamp)

    def records(self):
        return [j for j in journal.state()['journals'] if j.get('execution')]

    def test_unconfigured_worker_waits_without_account_or_model_requests(self):
        worker = recorder.Recorder(threading.Lock())
        with patch.object(recorder, 'credentials', return_value=('', '')), \
             patch.object(worker, 'poll') as poll, patch.object(worker, 'process_ai') as ai, \
             patch.object(worker, 'update_status') as status, \
             patch.object(worker.stop, 'wait', side_effect=lambda _: worker.stop.set()):
            worker.run()
        poll.assert_not_called()
        ai.assert_not_called()
        self.assertIn('unconfigured', [call.kwargs.get('status') for call in status.call_args_list])

    def test_19_fills_one_cycle_restart_idempotence(self):
        fills=[fill(i,'BUY',1) for i in range(1,20)]
        self.put(fills,[pos(19)])
        self.put(fills,[pos(19)])
        records=self.records()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['execution']['entry_quantity'],'19')
        self.assertEqual(len(records[0]['events']),19)
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auto_reviews').fetchone()[0],1)

    def test_scale_in_out_flat_then_new_entry(self):
        self.put([fill(1,'BUY',2)],[pos(2)])
        first=self.records()[0]['id']
        journal.save_journal({'id':first,'thesis':'原始判断','stop_loss':'90'})
        self.put([fill(2,'BUY',1),fill(3,'SELL',1)],[pos(2)])
        self.put([fill(4,'SELL',2)],[])
        self.put([fill(5,'BUY',4)],[pos(4)])
        records=self.records()
        self.assertEqual(len(records),2)
        old=next(j for j in records if j['id']==first)
        self.assertEqual(old['status'],'已平仓')
        self.assertEqual(old['thesis'],'原始判断')
        self.assertEqual(old['stop_loss'],'90')
        self.assertEqual({e['kind'] for e in old['events']},{'open','add','reduce','close'})
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auto_reviews').fetchone()[0],3)

    def test_open_close_between_polls_is_recorded(self):
        self.put([fill(1,'BUY',3),fill(2,'SELL',3)],[])
        j=self.records()[0]
        self.assertEqual(j['status'],'已平仓')
        self.assertTrue(j['execution']['history_complete'])

    def test_hedge_positions_never_merge(self):
        self.put([fill(1,'BUY',2),fill(2,'SELL',3,ps='SHORT')],[pos(2),pos(-3,ps='SHORT')])
        records=self.records()
        self.assertEqual(len(records),2)
        self.assertEqual({j['direction'] for j in records},{'LONG','SHORT'})

    def test_reversal_fee_and_realized_allocation(self):
        f=fill(2,'SELL',5,ps='BOTH');f.update(realizedPnl='3',commission='0.5')
        cycles,remaining=recorder.reconstruct('ABCUSDT:BOTH',[fill(1,'BUY',2,ps='BOTH'),f],'0')
        self.assertEqual(remaining,D('-3'))
        self.assertEqual(len(cycles),2)
        self.assertEqual(cycles[0]['realized_pnl'],'3')
        self.assertEqual(cycles[1]['realized_pnl'],'0')
        self.assertEqual(D(cycles[0]['fees']['USDT'])+D(cycles[1]['fees']['USDT']),D('.51'))

    def test_missing_opening_is_explicit_no_fabricated_entry(self):
        self.put([fill(1,'SELL',1)],[pos(2)])
        j=self.records()[0]
        self.assertFalse(j['execution']['history_complete'])
        self.assertEqual(j['entry_price'],'')
        self.assertEqual(j['opened_at'],'')

    def test_unmatched_positions_do_not_fabricate_closure(self):
        self.put([fill(1,'BUY',2)],[pos(2)])
        _,issues=self.put([],[])
        self.assertTrue(issues)
        self.assertEqual(self.records()[0]['status'],'持仓中')

    def test_network_failure_preserves_existing_position_snapshot(self):
        self.put([fill(1,'BUY',2)],[pos(2)])
        worker=recorder.Recorder(threading.Lock())
        with patch.object(recorder,'Binance',side_effect=BinanceError('offline')):
            with self.assertRaises(BinanceError):worker.poll()
        self.assertEqual(self.records()[0]['status'],'持仓中')
        self.assertFalse(worker.sync_lock.locked())

    def test_pending_order_quantity_excludes_already_filled_part(self):
        from trade_context import normalize_orders
        order=normalize_orders([dict(symbol='ABCUSDT',side='BUY',positionSide='LONG',type='LIMIT',origQty='100',executedQty='25',status='PARTIALLY_FILLED')])[0]
        self.assertEqual(order['quantity'],'75')

    def test_market_timer_does_not_slide_on_each_position_poll(self):
        worker=recorder.Recorder(threading.Lock())
        worker.market_at=1000000
        worker.capture_market([],set(),1015000)
        self.assertEqual(worker.market_at,1000000)
        worker.capture_market([],set(),1300000)
        self.assertEqual(worker.market_at,1300000)

    def test_existing_manual_journal_is_linked_not_replaced(self):
        create_journal()
        f=fill(1,'BUY',2,symbol='ETHUSDT',time=2000000000000)
        from datetime import datetime
        opened=datetime.fromtimestamp(f['time']/1000,recorder.HK).strftime('%Y-%m-%dT%H:%M:%S')
        journal.save_journal({'id':'fixture-eth','direction':'LONG','opened_at':opened,'thesis':'手写理由','entry_price':'99'})
        self.put([f],[pos(2,'ETHUSDT')])
        j=self.records()[0]
        self.assertEqual(j['id'],'fixture-eth')
        self.assertEqual(j['thesis'],'手写理由')
        self.assertEqual(j['entry_price'],'99')

    def test_position_and_order_snapshots_persist_without_duplicate_event(self):
        self.put([fill(1,'BUY',2)],[pos(2)])
        ctx={'ABCUSDT':{'status':'complete','captured_at':2000000,'orders':[]}}
        with journal.connect() as db:
            recorder.capture_positions(db,[pos(2)],ctx,2000000)
            recorder.capture_positions(db,[pos(2)],ctx,2015000)
        j=self.records()[0]
        self.assertEqual(j['capture_count'],2)
        self.assertEqual(len([e for e in j['events'] if e['kind']=='orders']),1)

if __name__=='__main__':unittest.main()
