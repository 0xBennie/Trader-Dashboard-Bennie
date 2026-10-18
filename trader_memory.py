"""Private, source-linked trading memory. User statements never become inferred personality."""
import json
import os
import tempfile
import uuid
import threading
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

import journal
import review_learning

DOCUMENT_LOCK = threading.Lock()


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS trader_memory_notes (
        id TEXT PRIMARY KEY, created INTEGER NOT NULL, updated INTEGER NOT NULL, data TEXT NOT NULL)''')


def notes(db):
    return [dict(json.loads(r['data']),id=r['id'],created=r['created'],updated=r['updated'])
            for r in db.execute('SELECT * FROM trader_memory_notes ORDER BY created,id')]


def save(payload):
    text = payload.get('text')
    if not isinstance(text,str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('请填写 1–2000 字的记忆内容。')
    kind = payload.get('kind','preference')
    if kind not in {'preference','risk_rule','lesson','hypothesis'}:
        raise ValueError('记忆类型不正确。')
    state = payload.get('state','confirmed')
    if state not in {'confirmed','hypothesis','archived'}:
        raise ValueError('记忆状态不正确。')
    source = str(payload.get('source','用户主动填写'))[:500]
    jid = payload.get('journal_id') or None
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if jid and not db.execute('SELECT 1 FROM journals WHERE id=?',(jid,)).fetchone():
            raise ValueError('关联交易不存在。')
        old = db.execute('SELECT * FROM trader_memory_notes WHERE id=?',(payload.get('id'),)).fetchone()
        if payload.get('id') and not old:
            raise ValueError('这条记忆不存在。')
        if old and payload.get('version') != old['updated']:
            raise journal.JournalConflict('记忆已有更新，请刷新后再修改。')
        data = dict(text=text.strip(),kind=kind,state=state,source=source,journal_id=jid,
                    history=(json.loads(old['data']).get('history',[])+[dict(data=json.loads(old['data'])['text'],state=json.loads(old['data'])['state'],at=old['updated'])]) if old else [])
        for key in ('risk_budget_lower','risk_budget_upper','risk_currency'):
            data[key]=payload.get(key,json.loads(old['data']).get(key,'') if old else '')
        if data['risk_budget_lower'] or data['risk_budget_upper']:
            try:
                lower,upper=Decimal(str(data['risk_budget_lower'])),Decimal(str(data['risk_budget_upper']))
                if not lower.is_finite() or not upper.is_finite() or not 0<lower<=upper or kind!='risk_rule' or data['risk_currency'] not in ('USD','USDT'):
                    raise ValueError()
            except (ValueError,InvalidOperation):
                raise ValueError('风险区间需为正数、下限不高于上限，并选择 USD 或 USDT。') from None
            data.update(risk_budget_lower=str(lower),risk_budget_upper=str(upper))
        now=max(journal.now_ms(),old['updated']+1 if old else 0); identity=old['id'] if old else uuid.uuid4().hex[:12]
        db.execute('INSERT OR REPLACE INTO trader_memory_notes VALUES(?,?,?,?)',
                   (identity,old['created'] if old else now,now,json.dumps(data,ensure_ascii=False)))
    refresh_document()
    return dict(data,id=identity,updated=now)


def snapshot(symbol=None, exclude=None):
    with journal.connect() as db:
        entries = notes(db)
        records = journal.all_journals(db)
        actions = [dict(r) for r in db.execute('SELECT journal_id,instruction,created FROM review_actions WHERE archived=0 ORDER BY created DESC LIMIT 12')]
    cases=[]
    for r in records:
        if r['id']==exclude or not any(str(r.get(k) or '').strip() for k in ('thesis','reflection','review_next')):
            continue
        cases.append(dict(journal_id=r['id'],exchange=r.get('exchange','binance'),symbol=r['symbol'],
                          direction=r.get('direction'),recorded_at=r['created'],updated_at=r['updated'],
                          original_reason=r.get('thesis',''),invalidation=r.get('invalidation',''),
                          reflection=r.get('reflection',''),next_action=r.get('review_next',''),
                          confirmed_closed=bool(review_learning.closed(r)),
                          source=r.get('source','用户日志'),
                          outcome_note='已确认平仓；盈亏查看原交易证据' if review_learning.closed(r) else '平仓与最终结果未核对'))
    base = str(symbol or '').removesuffix('USDT')
    cases.sort(key=lambda c:(c['symbol'].removesuffix('USDT')==base,c['updated_at']),reverse=True)
    return dict(updated_at=max([0]+[n['updated'] for n in entries]+[r['updated'] for r in records]),
                notes=entries,cases=cases,actions=actions,
                boundary='原始理由是用户当时的表述；相似案例不代表策略有优势。待验证假设不能写成已知缺点。未填写理由不等于没有计划。')


def context(symbol=None, exclude=None):
    data=snapshot(symbol,exclude)
    return dict(updated_at=data['updated_at'],boundary=data['boundary'],
                confirmed=sorted([n for n in data['notes'] if n['state']=='confirmed'],key=lambda n:n['updated'],reverse=True)[:20],
                hypotheses=[n for n in data['notes'] if n['state']=='hypothesis'][:10],
                related_cases=data['cases'][:5],user_actions=data['actions'][:6])


def risk_preference(memory):
    rules=[n for n in memory.get('confirmed',[]) if n.get('kind')=='risk_rule' and n.get('risk_budget_upper')]
    return max(rules,key=lambda n:n['updated']) if rules else None


def markdown(data):
    lines=['# 我的交易记忆','','这是本实例的私人文档，由已保存的记忆、用户原话和复盘记录生成。修改请在网站个人记忆中完成，历史版本保留。',
           '',data['boundary'],'','## 我确认的偏好与规则','']
    def one(n):
        return f"- [{n['id']}] {n['text']}\n  - 来源：{n['source']}"+(f"；关联日志：{n['journal_id']}" if n['journal_id'] else '')
    lines += [one(n) for n in data['notes'] if n['state']=='confirmed'] or ['尚无已确认的长期规则。']
    lines += ['','## 待验证的经验','']
    lines += [one(n) for n in data['notes'] if n['state']=='hypothesis'] or ['暂无；不会自动从盈亏推断个人缺点或盈利模式。']
    lines += ['','## 交易案例与原始表述','']
    for c in data['cases']:
        stamp=datetime.fromtimestamp(c['recorded_at']/1000,ZoneInfo('Asia/Hong_Kong')).isoformat()
        lines += [f"### {c['symbol']} · {c['exchange']} · {c['journal_id']}",f"记录于 {stamp}（不是入场时间）",'']
        for k,label in [('original_reason','原始理由'),('invalidation','用户失效条件'),('reflection','个人复盘'),('next_action','下一笔改进')]:
            if c[k]: lines += [f"{label}：{c[k]}",'']
        lines += [c['outcome_note'],'']
    lines += ['## 已保存的下一笔提醒','']+[f"- {a['instruction']}（日志 {a['journal_id']}）" for a in data['actions']]
    return '\n'.join(lines)+'\n'


def refresh_document():
    with DOCUMENT_LOCK:
        return _refresh_document()


def _refresh_document():
    data=snapshot(); content=markdown(data)
    path=journal.DB.parent/'trader-memory.md'
    if path.exists() and path.read_text()==content:
        return path
    fd,name=tempfile.mkstemp(prefix='.memory-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as output:
            output.write(content)
        os.chmod(name,0o600)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
    return path
