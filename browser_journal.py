"""Import facts observed in an authorized Variational browser. No exchange requests.

Usage: python browser_journal.py observation.json
See skills/trade-entry-interview/references/automatic-review.md for the schema.
"""
import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import journal
import review_learning


def number(value, positive=False):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or (positive and n <= 0):
            raise ValueError()
        return str(n)
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError('浏览器价格或数量格式无效。') from None


def validate(payload):
    now = journal.now_ms()
    observed = payload.get('observed_at')
    if isinstance(observed, bool) or not isinstance(observed, int) or not 0 < observed <= now+60000:
        raise ValueError('请提供有效的实际观察时间。')
    url = urlsplit(str(payload.get('source_url', '')))
    if url.scheme != 'https' or url.hostname != 'omni.variational.io' or url.username or url.password or url.query or url.fragment:
        raise ValueError('来源必须是 Variational 官方页面，不含身份信息或查询参数。')
    if payload.get('status') not in ('complete', 'login_required', 'unavailable', 'partial'):
        raise ValueError('请注明本次浏览器读取状态。')
    rows = payload.get('records', [])
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('单次最多处理 100 条观察。')
    if payload['status'] in ('login_required','unavailable') and rows:
        raise ValueError('无法读取页面时不能提交新的仓位事实。')
    clean = []
    for row in rows:
        identity = row.get('identity')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_.:@+-]{1,160}', identity):
            raise ValueError('请提供由成交标识和开仓时间构成的稳定记录标识。')
        symbol = str(row.get('symbol','')).upper()
        if not re.fullmatch('[A-Z0-9]{1,24}', symbol) or row.get('direction') not in ('LONG','SHORT'):
            raise ValueError('合约或方向无效。')
        value = {k:row.get(k) for k in ('journal_id','opened_at') if row.get(k)}
        if value.get('opened_at'):
            parsed = datetime.fromisoformat(value['opened_at'])
            if parsed.tzinfo is None or parsed.timestamp()*1000 > observed:
                raise ValueError('实际开仓时间必须含时区且不晚于观察时间。')
        value.update(identity=identity,symbol=symbol,direction=row['direction'],
                     entry_price=number(row.get('entry_price'),True),quantity=number(row.get('quantity'),True))
        position = row.get('position') or {}
        value['position'] = {k:number(position[k]) for k in ('mark_price','unrealized_pnl','notional','liquidation_price') if position.get(k) is not None}
        orders = row.get('orders')
        if orders is not None:
            if not isinstance(orders,list) or len(orders)>50:
                raise ValueError('挂单格式无效。')
            value['orders'] = []
            for order in orders:
                if order.get('kind') not in ('stop_loss','take_profit') or not isinstance(order.get('close_position'),bool):
                    raise ValueError('挂单类型或平仓范围无效。')
                value['orders'].append(dict(kind=order['kind'],price=number(order.get('price'),True),close_position=order['close_position']))
        else:
            value['orders'] = None  # Unknown is distinct from a successfully read empty table.
        close = row.get('close')
        if close:
            c = dict(source='authorized_browser',trade_status=close.get('trade_status'),
                     order_status=close.get('order_status'),reduce_only=close.get('reduce_only'),
                     closed_ms=close.get('closed_ms'),quantity=number(close.get('quantity'),True),
                     close_price=number(close.get('close_price'),True),
                     trade_display_id=str(close.get('trade_display_id',''))[:160],recorded_at=observed,
                     fees=None,funding=None,net_pnl=None,api_reconciled=False)
            if not c['trade_display_id'] or not review_learning.closed(dict(value,exchange='variational',status='已平仓',browser_close=c)) or c['closed_ms']>observed:
                raise ValueError('缺少全仓平仓的成交证据；不能用持仓消失推断平仓。')
            if close.get('realized_pnl_display') is not None:
                asset=close.get('realized_pnl_asset')
                if asset not in ('USD','USDC','USDT'):
                    raise ValueError('请保留页面盈亏的实际计价资产。')
                c.update(realized_pnl_display=number(close['realized_pnl_display']),realized_pnl_asset=asset)
            value['close'] = c
        clean.append(value)
    return observed, clean


def _ingest(payload):
    observed, rows = validate(payload)
    imported=[]
    for row in rows:
        binding='variational_identity:'+row['identity']
        with journal.connect() as db:
            records=journal.all_journals(db)
            jid=journal.kv(binding,None,db) or row.get('journal_id')
            existing=next((j for j in records if j['id']==jid),None)
            if jid and not existing:
                raise ValueError('关联日志不存在。')
            if existing and (existing.get('exchange')!='variational' or existing['symbol']!=row['symbol'] or existing['direction']!=row['direction']):
                raise ValueError('关联记录的交易所、合约或方向不符。')
            previous=(existing or {}).get('browser_observation') or {}
            if previous and previous.get('identity')!=row['identity']:
                raise ValueError('不同交易轮次不能覆盖同一记录。')
            if previous.get('observed_at',0)>=observed:
                imported.append(dict(id=jid,unchanged=True));continue
            if row.get('close') and existing and Decimal(row['quantity'])!=Decimal(existing['quantity']):
                raise ValueError('平仓数量与原日志不符，请先核对加减仓过程，不能直接标为全仓平仓。')
        values=dict(exchange='variational',symbol=row['symbol'],direction=row['direction'])
        if existing:
            values.update(id=jid,base_values={k:existing.get(k,'') for k in ('status','entry_price','quantity','opened_at')})
        else:
            values.update(title=row['symbol']+' · 浏览器交易记录',emotion='未记录',timeframe='5m')
        if not (existing or {}).get('browser_close'):
            values.update(status='已平仓' if row.get('close') else '持仓中',entry_price=row['entry_price'],quantity=row['quantity'])
        if row.get('opened_at') and not (existing or {}).get('opened_at'):
            from zoneinfo import ZoneInfo
            values['opened_at']=datetime.fromisoformat(row['opened_at']).astimezone(ZoneInfo('Asia/Hong_Kong')).replace(tzinfo=None).isoformat()
        saved=journal.save_journal(values)
        with journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM journals WHERE id=?',(saved['id'],)).fetchone()
            if current['updated']!=saved['updated']:
                raise journal.JournalConflict('日志已有更新，请重新读取后导入。')
            data=json.loads(current['data'])
            data['browser_observation']=dict(row,observed_at=observed,source='authorized_browser',source_url=payload['source_url'])
            if row.get('close'):
                data['browser_close']=dict(data.get('browser_close') or {},**row['close'])
            if not existing:
                data['source']='经授权浏览器观察；非交易所 API 对账'
                data['entry_interview']={'status':'awaiting_reason','phase':'post_entry','decision_timeframe':None}
            db.execute('INSERT INTO revisions(journal_id,time,data) VALUES(?,?,?)',(saved['id'],current['updated'],current['data']))
            db.execute('UPDATE journals SET data=?,updated=? WHERE id=?',(json.dumps(data,ensure_ascii=False),journal.now_ms(),saved['id']))
            journal.set_kv(db,binding,saved['id'])
            event=hashlib.sha256((row['identity']+':'+str(observed)).encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO browser_observations VALUES(?,?,?,?)',(event,saved['id'],observed,json.dumps(data['browser_observation'],ensure_ascii=False)))
        imported.append(dict(id=saved['id'],unchanged=False))
    with journal.connect() as db:
        old=journal.kv('variational_browser_status',{},db)
        if observed>=old.get('observed_at',0):
            journal.set_kv(db,'variational_browser_status',dict(status=payload['status'],observed_at=observed,
                last_success=observed if payload['status']=='complete' else old.get('last_success'),records=len(rows),
                note='经授权浏览器观察；非持续 API 同步。未出现的仓位不会自动标为平仓。'))
    return {'records':imported,'status':payload['status']}


def ingest(payload):
    # Serialize CLI/agent writers across processes without exposing a new public endpoint.
    import fcntl
    import os
    fd=os.open(journal.DB.with_suffix('.browser.lock'),os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            return _ingest(payload)
        except Exception:
            with journal.connect() as db:
                old=journal.kv('variational_browser_status',{},db)
                journal.set_kv(db,'variational_browser_status',dict(old,status='partial',
                    attempted_at=journal.now_ms(),note='本次导入未全部完成；已保存记录保留，可按稳定标识重试。'))
            raise


if __name__=='__main__':
    from pathlib import Path
    print(json.dumps(ingest(json.loads(Path(sys.argv[1]).read_text())),ensure_ascii=False))
