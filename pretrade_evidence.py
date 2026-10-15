"""Fresh, read-only evidence. Failed requests never become empty/zero evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import json
import threading
import journal
from binance_client import Binance, BinanceError, credentials
from trade_context import normalize_orders

SYNC_LOCK = threading.Lock()  # Server replaces this with the recorder's lock.


def _position_signature(rows):
    return sorted((r['symbol'],r.get('positionSide','BOTH'),str(Decimal(r['positionAmt']).normalize()),
                   str(Decimal(r.get('entryPrice','0')).normalize())) for r in rows if Decimal(r['positionAmt']))


def _income_signature(rows):
    return sorted((str(r.get('tranId')),r.get('incomeType'),r.get('symbol',''),r.get('asset'),str(r.get('income')))
                  for r in rows)


def refresh_ledger(client, result, start, initial_income):
    """Refresh the count's actual fills, not just a recent-looking account snapshot."""
    if not SYNC_LOCK.acquire(timeout=30):
        raise ValueError('成交采集正在运行，请完成后重试审核。')
    try:
        symbols={p['symbol'] for p in result['positions']['rows'] if Decimal(p['positionAmt'])}
        symbols.update(r.get('symbol') for r in initial_income if r.get('symbol'))
        with journal.connect() as db:
            symbols.update(j['symbol'] for j in journal.all_journals(db))
        symbols={s for s in symbols if s.endswith('USDT')}
        if len(symbols)>50: raise ValueError('当日需要核对的合约超过50个，未生成有效审核。')
        through=journal.now_ms()+client.offset
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(client.trades,symbol,start,through) for symbol in sorted(symbols)]
            fills=[fill for future in futures for fill in future.result()]
        positions=client.get('/fapi/v3/positionRisk',signed=True)
        final_income=client.income(start,journal.now_ms()+client.offset)
        if _position_signature(positions)!=_position_signature(result['positions']['rows']) or _income_signature(final_income)!=_income_signature(initial_income):
            raise ValueError('核对期间仓位或账单发生变化，请重新审核以纳入新成交。')
        import recorder
        import training
        stamp=journal.now_ms()
        with journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for fill in fills:
                db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?)',(fill['symbol'],str(fill['id']),fill['time'],json.dumps(fill)))
            for row in final_income:
                db.execute('INSERT OR REPLACE INTO income VALUES(?,?,?,?)',(row['incomeType'],str(row['tranId']),row['time'],json.dumps(row)))
            _,issues=recorder.reconcile(db,positions,stamp,journal.kv('recorder_settings',{},db).get('auto_ai',True))
            if issues: raise ValueError('成交与实际仓位尚未对齐，请完成同步后重新审核。')
            training.reconcile_audit(db,stamp)
            day_fills=[json.loads(r[0]) for r in db.execute('SELECT raw FROM trades WHERE time>=? AND time<=?',(start,through))]
            order_count=len({(r['symbol'],str(r.get('orderId',r['id']))) for r in day_fills})
        return {'complete':True,'captured_at':stamp,'day_start':start,'through':through-client.offset,
                'fill_count':len(day_fills),'order_count':order_count}
    finally:
        SYNC_LOCK.release()


def collect(intent, client=None):
    symbol = intent['symbol']
    client = client or Binance(*credentials(journal.ROOT))
    result = {'captured_at': journal.now_ms(), 'errors': []}
    try:
        client.align_time()
    except Exception:
        result['errors'].append('无法校准交易所时间，证据未刷新。')
        return result
    hk = timezone(timedelta(hours=8))
    start = int(datetime.combine(datetime.now(hk).date(), time.min, tzinfo=hk).timestamp()*1000)
    def quote():
        row = client.get('/fapi/v1/premiumIndex', {'symbol':symbol})
        price = Decimal(str(row['markPrice']))
        if not price.is_finite() or price <= 0: raise ValueError()
        # Use exchange update time, translated to the local clock used by other checks.
        captured = int(row['time'])-client.offset
        return {'symbol':symbol,'price':str(price),'captured_at':captured,'source':'Binance 标记价'}
    def positions():
        rows = client.get('/fapi/v3/positionRisk', signed=True)
        if not isinstance(rows,list): raise ValueError()
        fields = ('symbol','positionSide','positionAmt','entryPrice','markPrice','unRealizedProfit','liquidationPrice')
        return {'captured_at':journal.now_ms(),'rows':[{k:r.get(k) for k in fields} for r in rows], 'complete':True}
    def orders():
        rows = normalize_orders(client.get('/fapi/v1/openOrders',{'symbol':symbol},True))
        rows += normalize_orders(client.get('/fapi/v1/openAlgoOrders',{'symbol':symbol},True))
        return {'captured_at':journal.now_ms(),'rows':rows,'complete':True}
    def account():
        rows = client.income(start, journal.now_ms()+client.offset)
        kinds = {'REALIZED_PNL','COMMISSION','FUNDING_FEE','INSURANCE_CLEAR','COMMISSION_REBATE','API_REBATE'}
        net = sum((Decimal(str(r['income'])) for r in rows if r.get('asset')=='USDT' and r.get('incomeType') in kinds),Decimal(0))
        if not net.is_finite(): raise ValueError('当日损益数值无效。')
        other_assets = sorted({r.get('asset') for r in rows if r.get('asset')!='USDT' and r.get('incomeType') in kinds})
        if other_assets: raise ValueError('当日费用含其他币种，无法完整计算 USDT 日损失。')
        return {'captured_at':journal.now_ms(),'daily_realized_pnl_usdt':str(net),'day_start':start,'complete':True,
                'note':'香港自然日已实现盈亏、手续费及资金费；不含浮动盈亏。','_income_rows':rows}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {name:pool.submit(fn) for name,fn in [('quote',quote),('positions',positions),('orders',orders),('account',account)]}
        for name,future in tasks.items():
            try: result[name] = future.result()
            except Exception as error:
                result['errors'].append(name+' 读取未完成；'+(str(error) if isinstance(error,BinanceError) else '请重新读取。'))
    initial_income=result.get('account',{}).pop('_income_rows',None)
    if not result['errors'] and initial_income is not None:
        try:
            result['ledger']=refresh_ledger(client,result,start,initial_income)
            result['quote']=quote()  # Counting fills can take time; never reuse a stale mark.
        except Exception as error:
            result['errors'].append(str(error) if isinstance(error,(ValueError,BinanceError)) else '当日成交核对失败，不能确认新增风险次数。')
    return result
