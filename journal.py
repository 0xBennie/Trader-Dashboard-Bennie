import json
import sqlite3
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "journal.sqlite3"
NUMERIC = {"entry_price", "quantity", "stop_loss", "take_profit", "protect_trigger_pct", "protect_price"}
TEXT = {"title", "thesis", "invalidation", "exit_plan", "reflection", "emotion", "tags"}


class JournalConflict(ValueError):
    pass


def now_ms():
    return int(time.time() * 1000)


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades (symbol TEXT, id TEXT, time INTEGER, raw TEXT, PRIMARY KEY(symbol,id));
        CREATE TABLE IF NOT EXISTS income (type TEXT, id TEXT, time INTEGER, raw TEXT, PRIMARY KEY(type,id));
        CREATE TABLE IF NOT EXISTS exchange_trades (exchange TEXT, symbol TEXT, id TEXT, time INTEGER, raw TEXT, PRIMARY KEY(exchange,symbol,id));
        CREATE TABLE IF NOT EXISTS exchange_income (exchange TEXT, type TEXT, id TEXT, time INTEGER, raw TEXT, PRIMARY KEY(exchange,type,id));
        CREATE TABLE IF NOT EXISTS journals (id TEXT PRIMARY KEY, created INTEGER, updated INTEGER, data TEXT);
        CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, journal_id TEXT, time INTEGER, data TEXT);
        CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, journal_id TEXT, created INTEGER, status TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS position_snapshots (id INTEGER PRIMARY KEY, journal_id TEXT, captured INTEGER, data TEXT);
        CREATE INDEX IF NOT EXISTS snapshots_journal_time ON position_snapshots(journal_id,captured);
        CREATE TABLE IF NOT EXISTS trade_events (id TEXT PRIMARY KEY, journal_id TEXT, occurred INTEGER, kind TEXT, data TEXT);
        CREATE INDEX IF NOT EXISTS events_journal_time ON trade_events(journal_id,occurred);
        CREATE TABLE IF NOT EXISTS market_snapshots (id INTEGER PRIMARY KEY, journal_id TEXT, captured INTEGER, data TEXT);
        CREATE TABLE IF NOT EXISTS auto_reviews (event_id TEXT PRIMARY KEY, journal_id TEXT, due INTEGER, status TEXT, analysis_id TEXT);
        ''')
        import sharing
        sharing.init(db)
        import review_learning
        review_learning.init(db)
    DB.chmod(0o600)


def kv(key, default=None, db=None):
    if db is None:
        with connect() as conn:
            return kv(key, default, conn)
    row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_kv(db, key, value):
    db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
    if key in ('snapshot', 'exchange:okx:snapshot', 'exchange:bybit:snapshot'):
        import sharing
        sharing.record_coverage(db, 'binance' if key == 'snapshot' else key.split(':')[1], value)


def all_journals(db):
    return [dict(json.loads(r["data"]), id=r["id"], created=r["created"], updated=r["updated"])
            for r in db.execute("SELECT * FROM journals ORDER BY created DESC")]


def review_progress(db, records, stamp=None):
    """Personal reflection and AI coverage share a policy-independent 30-day window.

    Trace the reflection's own version, not the timestamp of an unrelated edit.
    A pre-close observation must not silently become a completed review.
    """
    stamp = now_ms() if stamp is None else stamp
    start = stamp - 30 * 86400000
    items = []
    for item in records:
        closed = (item.get("execution") or {}).get("closed_ms")
        import review_learning
        texts = review_learning.content(item)
        versions = [json.loads(row['data']) | {'_time': row['time']} for row in
                    db.execute('SELECT time,data FROM revisions WHERE journal_id=? ORDER BY id DESC', (item['id'],))] if any(texts) else []
        written = []
        for key, text in zip(review_learning.TEXT_FIELDS, texts):
            if not text:
                continue
            changed_at = item['updated']
            for revision in versions:
                if str(revision.get(key) or '').strip() != text:
                    break
                changed_at = revision['_time']
            written.append(changed_at)
        reflected_at = max(written) if written else None
        report = db.execute("SELECT created FROM analyses WHERE journal_id=? AND status='complete' AND created>=? ORDER BY created DESC LIMIT 1",
                            (item["id"], closed)).fetchone() if closed else None
        status = {"eligible": bool(closed and start <= closed <= stamp),
                  "personal_complete": bool(closed and reflected_at is not None and closed <= reflected_at <= stamp),
                  "personal_at": reflected_at, "ai_complete": bool(report),
                  "ai_at": report["created"] if report else None, "closed_at": closed}
        item["review_status"] = status
        if status["eligible"]:
            items.append(dict(status, id=item["id"], symbol=item["symbol"], title=item.get("title", ""), exchange=item.get("exchange", "binance")))
    items.sort(key=lambda x: x["closed_at"], reverse=True)
    total = len(items)
    def ratio(key):
        count = sum(x[key] for x in items)
        return {"numerator": count, "denominator": total, "ratio": str(Decimal(count) / total) if total else None}
    return {"scope": "closed_last_30_days", "start": start, "through": stamp,
            "personal": ratio("personal_complete"), "ai": ratio("ai_complete"),
            "pending": [x for x in items if not x["personal_complete"]],
            "completed": [x for x in items if x["personal_complete"]]}


def is_binance(record):
    # Older journals predate the exchange field and belong to the Binance ledger.
    return record.get("exchange", "binance") == "binance"


def binance_journals(db):
    return [record for record in all_journals(db) if is_binance(record)]


def save_journal(payload):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        jid = payload.get("id")
        old = db.execute("SELECT * FROM journals WHERE id=?", (jid,)).fetchone() if jid else None
        if jid and not old:
            raise ValueError("找不到这条记录。")
        previous = json.loads(old["data"]) if old else {}
        base = payload.get("base_values", {})
        if not isinstance(base, dict):
            raise ValueError("笔记版本格式不正确。")
        conflicts = [key for key, value in base.items() if key in payload
                     and str(previous.get(key, "")) != str(value)
                     and str(previous.get(key, "")) != str(payload[key])]
        if conflicts:
            raise JournalConflict("同一字段已有更新，未覆盖已保存版本。你的草稿仍保留，请刷新后核对冲突提示。")
        data = dict(previous)
        for key in TEXT:
            data[key] = str(payload.get(key, previous.get(key, "")))[:8000]
        import review_learning
        data.update(review_learning.fields(payload, previous))
        exchange = str(payload.get("exchange", previous.get("exchange", "binance"))).lower()
        if exchange not in {"binance", "variational", "okx", "bybit"}:
            raise ValueError("交易所不支持。")
        if old and exchange != previous.get("exchange", "binance"):
            raise ValueError("已有记录不能更换交易所，请新建独立记录。")
        symbol = str(payload.get("symbol", previous.get("symbol", "ETHUSDT"))).upper().strip()
        if not symbol.isascii() or not symbol.isalnum() or not 1 <= len(symbol) <= 24:
            raise ValueError("合约名称只能包含英文字母和数字。")
        if exchange != "variational" and (len(symbol) < 5 or not symbol.endswith("USDT")):
            raise ValueError("API 账户支持 USDT 永续交易对，例如 ETHUSDT。")
        data.update(symbol=symbol, exchange=exchange, quote_currency="USD" if exchange == "variational" else "USDT")
        for key in NUMERIC:
            val = str(payload.get(key, previous.get(key, ""))).strip()
            if val:
                try:
                    number = Decimal(val)
                    if not number.is_finite() or number <= 0:
                        raise ValueError("价格和数量必须是大于零的有限数字。")
                except InvalidOperation:
                    raise ValueError("价格和数量格式不正确。") from None
            data[key] = val
        for key, allowed, default in [("direction", {"LONG", "SHORT", "UNKNOWN"}, "UNKNOWN"),
                                       ("status", {"待核对", "计划中", "持仓中", "已平仓"}, "待核对")]:
            val = payload.get(key, previous.get(key, default))
            if val not in allowed:
                raise ValueError("记录状态或方向不正确。")
            data[key] = val
        opened_at = str(payload.get("opened_at", previous.get("opened_at", "")))[:32]
        if opened_at:
            from datetime import datetime
            try:
                datetime.fromisoformat(opened_at)
            except ValueError:
                raise ValueError("开仓时间格式不正确。") from None
        timeframe = str(payload.get("timeframe", previous.get("timeframe", "4h")))
        if timeframe not in {"5m", "15m", "20m", "30m", "1h", "4h", "1d"}:
            raise ValueError("图表周期不支持。")
        data.update(opened_at=opened_at, market={"binance":"Binance USDⓈ-M 永续","variational":"Variational 永续","okx":"OKX USDT 永续","bybit":"Bybit USDT 永续"}[exchange], timeframe=timeframe)
        data.setdefault("source", "用户手动记录；不等同于交易所成交")
        if old and data == previous:
            return dict(data, id=jid, updated=old["updated"], created=old["created"])
        stamp = now_ms()
        if old:
            db.execute("INSERT INTO revisions(journal_id,time,data) VALUES(?,?,?)", (jid, old["updated"], old["data"]))
            db.execute("UPDATE journals SET data=?,updated=? WHERE id=?", (json.dumps(data, ensure_ascii=False), stamp, jid))
        else:
            jid = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO journals VALUES(?,?,?,?)", (jid, stamp, stamp, json.dumps(data, ensure_ascii=False)))
        return dict(data, id=jid, updated=stamp, created=old["created"] if old else stamp)


def risk_math(j):
    try:
        entry, stop, target, quantity = [Decimal(j.get(k) or "NaN") for k in ("entry_price", "stop_loss", "take_profit", "quantity")]
        side = 1 if j.get("direction") == "LONG" else -1 if j.get("direction") == "SHORT" else 0
        if not side or not all(n.is_finite() for n in (entry, stop, target, quantity)):
            return {"valid": False, "reason": "补全方向、入场价、数量、止损与目标后计算。"}
        risk, reward = (entry - stop) * side * quantity, (target - entry) * side * quantity
        if risk <= 0 or reward <= 0:
            return {"valid": False, "reason": "止损或目标与交易方向不一致，请核对。"}
        return {"valid": True, "risk": str(risk), "reward": str(reward), "rr": str(reward / risk)}
    except (InvalidOperation, TypeError):
        return {"valid": False, "reason": "交易计划尚未填写完整。"}


def aggregate_income(income):
    totals = {}
    curve = []
    running = Decimal(0)
    for row in sorted(income, key=lambda r: r["time"]):
        asset, kind = row["asset"], row["incomeType"]
        if kind not in {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}:
            continue
        values = totals.setdefault(asset, {"realized": Decimal(0), "fees": Decimal(0), "funding": Decimal(0), "net": Decimal(0)})
        amount = Decimal(row["income"])
        values[{"REALIZED_PNL": "realized", "COMMISSION": "fees", "FUNDING_FEE": "funding"}[kind]] += amount
        values["net"] += amount
        if asset == "USDT":
            running += amount
            curve.append({"time": row["time"], "value": str(running)})
    return {k: {a: str(b) for a, b in v.items()} for k, v in totals.items()}, curve


def group_orders(trades):
    groups = {}
    for trade in trades:
        # Exchange IDs are kept as strings: JavaScript cannot safely represent every 64-bit ID.
        key = (trade["symbol"], str(trade.get("orderId", "fill:" + str(trade["id"]))))
        group = groups.setdefault(key, {"symbol": key[0], "order_key": key[1], "side": trade.get("side", ""),
            "positionSide": trade.get("positionSide", ""), "time": trade["time"], "lastTime": trade["time"],
            "qty": Decimal(0), "notional": Decimal(0), "realizedPnl": Decimal(0), "fees": {}, "fill_count": 0})
        qty = Decimal(trade.get("qty", "0"))
        group["qty"] += qty
        group["notional"] += qty * Decimal(trade.get("price", "0"))
        group["realizedPnl"] += Decimal(trade.get("realizedPnl", "0"))
        group["time"] = min(group["time"], trade["time"])
        group["lastTime"] = max(group["lastTime"], trade["time"])
        group["fill_count"] += 1
        asset = trade.get("commissionAsset", "USDT")
        group["fees"][asset] = group["fees"].get(asset, Decimal(0)) + Decimal(trade.get("commission", "0"))
    results = []
    for group in groups.values():
        group["price"] = str(group["notional"] / group["qty"]) if group["qty"] else "0"
        for name in ("qty", "notional", "realizedPnl"):
            group[name] = str(group[name])
        group["fees"] = {k: str(v) for k, v in group["fees"].items()}
        results.append(group)
    return sorted(results, key=lambda g: g["lastTime"], reverse=True)


def state():
    with connect() as db:
        sync = kv("sync", {}, db)
        snapshot = kv("snapshot", {}, db)
        coverage = snapshot.get("coverage", {})
        start, end = coverage.get("start", 0), coverage.get("end", now_ms())
        income = [json.loads(r[0]) for r in db.execute("SELECT raw FROM income WHERE time>=? AND time<=? ORDER BY time", (start, end))]
        trades = [json.loads(r[0]) for r in db.execute("SELECT raw FROM trades WHERE time>=? AND time<=? ORDER BY time DESC", (start, end))]
        for trade in trades:
            trade["order_key"] = str(trade.get("orderId", "fill:" + str(trade["id"])))
        journals = all_journals(db)
        reviews = review_progress(db, journals)
        import review_learning
        learning = review_learning.summary(db, journals, now_ms())
        for j in journals:
            j["risk"] = risk_math(j)
            j["capture_count"] = db.execute("SELECT COUNT(*) FROM position_snapshots WHERE journal_id=?", (j["id"],)).fetchone()[0]
            j["captures"] = [dict(captured=r[0], **json.loads(r[1])) for r in db.execute("SELECT captured,data FROM position_snapshots WHERE journal_id=? ORDER BY captured DESC LIMIT 120", (j["id"],))]
            j["events"] = [dict(occurred=r[0],kind=r[1],**json.loads(r[2])) for r in db.execute("SELECT occurred,kind,data FROM trade_events WHERE journal_id=? ORDER BY occurred DESC LIMIT 60", (j["id"],))]
            j["market_captures"] = [dict(id=r[0],captured=r[1],**json.loads(r[2])) for r in db.execute("SELECT id,captured,data FROM market_snapshots WHERE journal_id=? ORDER BY captured DESC LIMIT 12", (j["id"],))]
        analyses = [dict(id=r["id"], journal_id=r["journal_id"], created=r["created"], status=r["status"], **json.loads(r["data"]))
                    for r in db.execute("SELECT * FROM analyses ORDER BY created DESC LIMIT 50")]
        totals, curve = aggregate_income(income)
        from analytics import build_analytics
        from exchange_sync import states
        return {"exchanges": states(db), "sync": sync, "snapshot": snapshot, "journals": journals, "trades": trades,
                "orders": group_orders(trades), "trade_context": kv("trade_context", {}, db),
                "recorder": kv("recorder_status", {}), "recorder_settings": kv("recorder_settings", {"enabled":True,"interval":15,"auto_ai":True}),
                "totals": totals, "curve": curve, "analytics": build_analytics(income, trades, coverage),
                "analyses": analyses, "review_progress": reviews, "review_learning": learning, "server_time": now_ms()}
