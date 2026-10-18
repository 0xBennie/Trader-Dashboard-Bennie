"""Durable, read-only account recorder. Runs without a browser; never submits orders."""
import hashlib
import json
import threading
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import journal
import ledger
import binance_account
import trade_context
from binance_client import Binance, BinanceError, credentials

D = Decimal
DEFAULTS = {"enabled": True, "interval": 15, "auto_ai": True}
HK = ZoneInfo("Asia/Hong_Kong")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()[:24]


def position_key(p):
    return p["symbol"] + ":" + p.get("positionSide", "BOTH")


def signed_qty(fill):
    return D(fill["qty"]) * (1 if fill["side"] == "BUY" else -1)


def reconstruct(key, fills, initial):
    """Split flat-to-flat cycles, including one-way reversals and partial executions."""
    symbol, position_side = key.split(":")
    cycles, current = [], None
    remaining = D(initial)

    def new_cycle(first, sign, unknown=False):
        return {"cycle_id": digest([key, "partial" if unknown else str(first["id"]), sign]),
                "symbol": symbol, "positionSide": position_side, "direction": "LONG" if sign > 0 else "SHORT",
                "opened_ms": None if unknown else int(first["time"]), "closed_ms": None,
                "history_complete": not unknown, "initial_quantity": str(abs(remaining)) if unknown else "0",
                "entry_quantity": D(0), "entry_notional": D(0), "exit_quantity": D(0),
                "realized_pnl": D(0), "fees": {}, "fills": [], "allocations": {}, "events": []}

    if remaining:
        current = new_cycle(fills[0] if fills else {"id": "unknown"}, 1 if remaining > 0 else -1, True)
    for fill in fills:
        delta = signed_qty(fill)
        closing = min(abs(delta), abs(remaining)) if remaining and remaining * delta < 0 else D(0)
        parts = []
        if closing:
            parts.append(("close", closing))
        if abs(delta) > closing:
            parts.append(("open", abs(delta) - closing))
        for operation, qty in parts:
            if current is None:
                current = new_cycle(fill, 1 if delta > 0 else -1)
            before = remaining
            change = qty * (1 if delta > 0 else -1)
            remaining += change
            if operation == "open":
                kind = "open" if not before else "add"
                current["entry_quantity"] += qty
                current["entry_notional"] += qty * D(fill["price"])
            else:
                kind = "close" if not remaining else "reduce"
                current["exit_quantity"] += qty
                # Realized P/L belongs entirely to the closing leg of a reversal.
                current["realized_pnl"] += D(fill.get("realizedPnl", "0"))
            asset = fill.get("commissionAsset", "USDT")
            fee = D(fill.get("commission", "0")) * qty / abs(delta)
            current["fees"][asset] = current["fees"].get(asset, D(0)) + fee
            current["fills"].append(str(fill["id"]))
            current["allocations"][str(fill["id"])] = {"qty":str(qty), "commission":str(fee), "realizedPnl":fill.get("realizedPnl","0") if operation == "close" else "0"}
            current["events"].append({"id": digest([current["cycle_id"], str(fill["id"]), operation]),
                "kind": kind, "time": fill["time"], "price": fill["price"], "quantity": str(qty),
                "order_key": str(fill.get("orderId", fill["id"])), "remaining": str(abs(remaining)), "side": fill["side"]})
            if not remaining:
                current["closed_ms"] = int(fill["time"])
                cycles.append(current)
                current = None
    if current:
        cycles.append(current)
    for cycle in cycles:
        cycle["remaining"] = str(abs(remaining)) if cycle is current else "0"
        cycle["entry_price"] = str(cycle["entry_notional"] / cycle["entry_quantity"]) if cycle["entry_quantity"] and cycle["history_complete"] else ""
        for field in ("entry_quantity", "entry_notional", "exit_quantity", "realized_pnl"):
            cycle[field] = str(cycle[field])
        cycle["fees"] = {k: str(v) for k, v in cycle["fees"].items()}
    return cycles, remaining


def reconcile(db, positions, stamp, queue_ai=True, exchange="binance"):
    """Only matched, fully fetched ledgers update cycles. Never infer closure from a failed read."""
    grouped = {}
    rows = db.execute("SELECT raw FROM trades ORDER BY time, CAST(id AS INTEGER)") if exchange == "binance" else db.execute("SELECT raw FROM exchange_trades WHERE exchange=? ORDER BY time, id", (exchange,))
    for row in rows:
        fill = json.loads(row[0])
        grouped.setdefault(position_key(fill), []).append(fill)
    if exchange != "binance":
        for fills in grouped.values():
            fills.sort(key=lambda f: (f["time"], int(f.get("sequence", "0")), str(f["id"])))
    live = {position_key(p): D(p["positionAmt"]) for p in positions if D(p["positionAmt"])}
    anchor_key = "recorder_anchors" if exchange == "binance" else "recorder_anchors:" + exchange
    anchors = journal.kv(anchor_key, {}, db)
    records = [j for j in journal.all_journals(db) if j.get("exchange", "binance") == exchange]
    issues, changed = [], set()
    for key in sorted(set(grouped) | set(live)):
        fills = grouped.get(key, [])
        first_id = str(fills[0]["id"]) if fills else "none"
        anchor = anchors.get(key)
        if anchor is None or anchor["first_id"] != first_id:
            initial = live.get(key, D(0)) - sum((signed_qty(f) for f in fills), D(0))
            anchor = {"first_id": first_id, "initial": str(initial)}
            anchors[key] = anchor
        cycles, remaining = reconstruct(key, fills, anchor["initial"])
        if remaining != live.get(key, D(0)):
            issues.append(key + " 仓位与成交尚未对齐，等待补读")
            continue
        for cycle in cycles:
            if exchange != "binance":
                cycle["cycle_id"] = exchange + ":" + cycle["cycle_id"]
                for event in cycle["events"]:
                    event["id"] = exchange + ":" + event["id"]
            existing = next((j for j in records if j.get("auto_cycle_id") == cycle["cycle_id"]), None)
            # Attach a previously written manual entry without replacing its rationale or plan.
            if not existing and cycle["opened_ms"]:
                for j in records:
                    if j.get("auto_cycle_id") or j["symbol"] != cycle["symbol"] or j.get("direction") != cycle["direction"]:
                        continue
                    try:
                        opened = datetime.fromisoformat(j.get("opened_at", "")).replace(tzinfo=HK).timestamp() * 1000
                    except ValueError:
                        continue
                    if abs(opened - cycle["opened_ms"]) < 300000:
                        existing = j
                        break
            jid = existing["id"] if existing else "auto-" + cycle["cycle_id"]
            events = cycle.pop("events")
            # Exchange position is a separate cost basis, not reconstructed from an incomplete opening.
            cycle["observed_at"] = stamp
            if existing:
                data = {k: v for k, v in existing.items() if k not in {"id", "created", "updated"}}
            else:
                data = {"title": cycle["symbol"].removesuffix("USDT") + " · 自动交易记录", "symbol": cycle["symbol"],
                    "market": {"binance": "Binance USDⓈ-M 永续", "okx": "OKX USDT 永续", "bybit": "Bybit USDT 永续"}[exchange], "exchange": exchange, "quote_currency": "USDT", "timeframe": "5m", "direction": cycle["direction"],
                    "entry_price": cycle["entry_price"], "quantity": cycle["entry_quantity"],
                    "opened_at": datetime.fromtimestamp(cycle["opened_ms"]/1000, HK).strftime("%Y-%m-%dT%H:%M:%S") if cycle["opened_ms"] else "",
                    "source": {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}[exchange] + " 成交自动归档", "emotion": "未记录", "thesis": "", "invalidation": "",
                    "exit_plan": "", "reflection": "", "stop_loss": "", "take_profit": "", "tags": ""}
            old_execution = data.get("execution", {})
            data.update(auto_cycle_id=cycle["cycle_id"], execution=cycle, status="已平仓" if cycle["closed_ms"] else "持仓中")
            if not existing:
                db.execute("INSERT INTO journals VALUES(?,?,?,?)", (jid, stamp, stamp, encoded(data)))
                records.append(dict(data, id=jid, created=stamp, updated=stamp))
                if not cycle["closed_ms"]:
                    changed.add(jid)
            else:
                db.execute("UPDATE journals SET data=? WHERE id=?", (encoded(data), jid))
            new_events = []
            for event in events:
                result = db.execute("INSERT OR IGNORE INTO trade_events VALUES(?,?,?,?,?)",
                    (event["id"], jid, event["time"], event["kind"], encoded({k:v for k,v in event.items() if k not in {"id","kind","time"}})))
                if result.rowcount:
                    new_events.append(event)
            if old_execution and new_events:
                changed.add(jid)
            # Backfilled old closed trades do not trigger a storm of AI calls.
            has_report = db.execute("SELECT 1 FROM analyses WHERE journal_id=? AND status IN ('running','complete')", (jid,)).fetchone()
            newly_closed = cycle["closed_ms"] and ((old_execution and not old_execution.get("closed_ms")) or (not existing and 0 <= stamp-cycle["closed_ms"] < 300000))
            trigger = "close" if newly_closed else "open" if not cycle["closed_ms"] and not has_report else None
            if queue_ai and trigger:
                db.execute("INSERT OR IGNORE INTO auto_reviews VALUES(?,?,?,?,NULL)",
                    (cycle["cycle_id"] + ":" + trigger, jid, stamp + 30000, "pending"))
    journal.set_kv(db, anchor_key, anchors)
    return changed, issues


def capture_positions(db, positions, contexts, stamp, exchange="binance"):
    live = {position_key(p): p for p in positions if D(p["positionAmt"])}
    changed = set()
    for j in [j for j in journal.all_journals(db) if j.get("exchange", "binance") == exchange]:
        execution = j.get("execution", {})
        if not execution or execution.get("closed_ms"):
            continue
        key = j["symbol"] + ":" + execution["positionSide"]
        p = live.get(key)
        if not p:
            continue  # No zero/closure is invented if the fill ledger has not reconciled yet.
        ctx = contexts.get(j["symbol"], {})
        payload = {"position": {k: p.get(k) for k in ("symbol","positionSide","positionAmt","entryPrice","markPrice","unRealizedProfit","breakEvenPrice","notional","updateTime")},
                   "orders_status": ctx.get("status", "pending"), "orders_at": ctx.get("captured_at"),
                   "orders": ctx.get("orders", []) if ctx.get("status") == "complete" else None}
        db.execute("INSERT INTO position_snapshots(journal_id,captured,data) VALUES(?,?,?)", (j["id"],stamp,encoded(payload)))
        if ctx.get("status") == "complete":
            signature = digest(ctx["orders"])
            previous = journal.kv("orders:" + j["id"], None, db)
            if signature != previous:
                changed.add(j["id"])
                db.execute("INSERT OR IGNORE INTO trade_events VALUES(?,?,?,?,?)", (digest([j["id"],"orders",stamp,signature]),j["id"],stamp,"orders",encoded({"orders":ctx["orders"],"initial":previous is None})))
                journal.set_kv(db, "orders:" + j["id"], signature)
    return changed


class Recorder:
    def __init__(self, sync_lock):
        self.sync_lock = sync_lock
        self.stop = threading.Event()
        self.client = None
        self.signature = None
        self.ledger_at = self.orders_at = self.market_at = self.aligned_at = 0

    def settings(self):
        return {**DEFAULTS, **journal.kv("recorder_settings", {})}

    def update_status(self, **values):
        with journal.connect() as db:
            status = journal.kv("recorder_status", {}, db)
            status.update(values)
            journal.set_kv(db, "recorder_status", status)

    def poll(self):
        settings = self.settings()
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            current = credentials(journal.ROOT)
            if self.client is None or (self.client.key, self.client.secret) != current:
                candidate = Binance(*current)
                candidate.align_time()
                binance_account.bind(candidate)
                self.client = candidate
                self.signature = None
                self.ledger_at = 0
            stamp = journal.now_ms()
            if stamp - self.aligned_at > 1800000:
                self.client.align_time()
                self.aligned_at = stamp
            positions = self.client.get("/fapi/v3/positionRisk", signed=True)
            if not isinstance(positions, list):
                raise BinanceError("仓位读取格式错误，保留上次快照。")
            positions = [p for p in positions if D(p["positionAmt"]) and p["symbol"].endswith("USDT")]
            signature = digest(sorted((position_key(p), p["positionAmt"], p["entryPrice"]) for p in positions))
            changed_positions = signature != self.signature
            changed, issues = set(), []
            if changed_positions or stamp - self.ledger_at >= 60000:
                end = journal.now_ms() + self.client.offset
                with journal.connect() as db:
                    snapshot = journal.kv("snapshot", {}, db)
                    checkpoint = journal.kv("recorder_checkpoint", snapshot.get("coverage", {}).get("end"), db)
                    days = max(7, min(90, snapshot.get("coverage", {}).get("days", 30)))
                    verified = ledger.ranges(db, "binance")
                    # Re-read 7 days for delayed postings; first upgraded sync revalidates legacy coverage.
                    start = min(int(checkpoint), end - 7*86400000) if checkpoint and verified else end - days*86400000
                    if end-start > 90*86400000:
                        raise BinanceError("离线超过 90 天，请手动同步并核对未覆盖的历史账单。")
                    known = {p["symbol"] for p in snapshot.get("positions", [])}
                    known.update(j["symbol"] for j in journal.binance_journals(db) if j.get("status") == "持仓中")
                income = self.client.income(start, end)
                ledger.classified(income, allow_unknown=True)
                symbols = known | {p["symbol"] for p in positions} | {i["symbol"] for i in income if i.get("symbol", "").endswith("USDT")}
                trades = []
                for symbol in sorted(symbols):
                    trades.extend(self.client.trades(symbol, start, end))
                # A trade during collection must not be attributed to a fictitious initial holding.
                after = self.client.get("/fapi/v3/positionRisk", signed=True)
                after = [p for p in after if D(p["positionAmt"]) and p["symbol"].endswith("USDT")]
                after_signature = digest(sorted((position_key(p), p["positionAmt"], p["entryPrice"]) for p in after))
                with journal.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("DELETE FROM income WHERE time BETWEEN ? AND ?", (start, end))
                    for t in trades:
                        db.execute("INSERT OR REPLACE INTO trades VALUES(?,?,?,?)", (t["symbol"],str(t["id"]),t["time"],encoded(t)))
                    for i in income:
                        db.execute("INSERT OR REPLACE INTO income VALUES(?,?,?,?)", (i["incomeType"],str(i["tranId"]),i["time"],encoded(i)))
                    if after_signature == signature:
                        changed, issues = reconcile(db, after, stamp, settings["auto_ai"])
                        import training
                        training.reconcile_audit(db, stamp)
                    else:
                        issues = ["采集期间仓位发生变化，下一轮重新核对成交"]
                    # Never move checkpoint past a mismatched batch; overlap also covers delayed income.
                    if not issues:
                        journal.set_kv(db, "recorder_checkpoint", end)
                    # Ledger coverage follows actual completed reads, not a sliding assumed start.
                    ledger.record_coverage(db, "binance", start, end)
                    left, right = ledger.ranges(db, "binance")[-1]
                    coverage = dict(snapshot.get("coverage", {}), start=left, end=right, days=days,
                                    symbols=sorted(symbols), complete=True, ledger_version=ledger.VERSION,
                                    scope="Binance USDⓈ-M；USDT 成交，全部账单币种分列")
                    snapshot.update(coverage=coverage)
                    journal.set_kv(db, "snapshot", snapshot)
                positions = after
                if not issues:
                    self.signature = after_signature
                    self.ledger_at = stamp
                self.update_status(ledger_at=stamp, ledger_issues=issues)
            contexts = journal.kv("trade_context", {})
            if changed_positions or stamp - self.orders_at >= 30000:
                symbols = {p["symbol"] for p in positions}
                contexts.update(trade_context.collect(self.client, symbols, positions))
                self.orders_at = stamp
            captured = journal.now_ms()
            with journal.connect() as db:
                snapshot = journal.kv("snapshot", {}, db)
                snapshot.update(positions=positions, captured_at=captured)
                journal.set_kv(db, "snapshot", snapshot)
                journal.set_kv(db, "trade_context", contexts)
                changed.update(capture_positions(db, positions, contexts, captured))
                records = journal.binance_journals(db)
            self.update_status(status="running", last_success=captured, error=None, orders_at=self.orders_at)
            self.capture_market(records, changed, stamp)
        finally:
            self.sync_lock.release()

    def capture_market(self, records, changed, stamp):
        due = stamp - self.market_at >= 300000
        if not due and not changed:
            return
        cache = {}
        errors = []
        for j in records:
            if not journal.is_binance(j) or not j.get("execution") or (j["status"] != "持仓中" and j["id"] not in changed):
                continue
            if not due and j["id"] not in changed:
                continue
            try:
                symbol = j["symbol"]
                if symbol not in cache:
                    rows = self.client.get("/fapi/v1/klines", {"symbol":symbol,"interval":"5m","limit":120})
                    observed = journal.now_ms()
                    closed = [r for r in rows if int(r[6]) < observed + self.client.offset]
                    cache[symbol] = {"symbol":symbol,"interval":"5m","source":"Binance USDⓈ-M","observed_at":observed,
                        "candles":[{"time":r[0],"open":r[1],"high":r[2],"low":r[3],"close":r[4],"volume":r[5]} for r in closed],
                        "note":"5 分钟已收盘 K 线；采集时回补最近 10 小时行情，非当时的订单簿或界面截图"}
                    trends = {}
                    for interval in ("1h", "4h"):
                        bars = self.client.get("/fapi/v1/klines", {"symbol":symbol,"interval":interval,"limit":80})
                        bars = [r for r in bars if int(r[6]) < observed + self.client.offset]
                        close = [D(r[4]) for r in bars]
                        trends[interval] = {"last_close":str(close[-1]), "last_close_at":bars[-1][6],
                            "sma20":str(sum(close[-20:])/20) if len(close)>=20 else None,
                            "sma50":str(sum(close[-50:])/50) if len(close)>=50 else None,
                            "last12_high":str(max(D(r[2]) for r in bars[-12:])),
                            "last12_low":str(min(D(r[3]) for r in bars[-12:]))}
                    cache[symbol]["trends"] = trends
                    try:
                        oi = self.client.get("/futures/data/openInterestHist", {"symbol":symbol,"period":"1h","limit":25})
                        if len(oi)>=2:
                            begin, end = oi[0], oi[-1]
                            cache[symbol]["open_interest"] = {"start_at":begin["timestamp"],"end_at":end["timestamp"],
                                "start_quantity":begin["sumOpenInterest"],"end_quantity":end["sumOpenInterest"],
                                "change_pct":str((D(end["sumOpenInterest"])/D(begin["sumOpenInterest"])-1)*100),
                                "note":"未平仓合约总量变化不能单独证明多空方向"}
                    except (BinanceError, KeyError, ValueError):
                        cache[symbol]["open_interest"] = {"status":"unavailable"}
                with journal.connect() as db:
                    db.execute("INSERT INTO market_snapshots(journal_id,captured,data) VALUES(?,?,?)", (j["id"],stamp,encoded(cache[symbol])))
            except (BinanceError, ValueError, KeyError, TypeError):
                errors.append(j["symbol"] + " 行情快照失败，将自动重试")
        if due and not errors:
            self.market_at = stamp
        self.update_status(market_at=self.market_at or None, market_errors=errors)

    def process_ai(self):
        import review_automation
        review_automation.process_queue()

    def run(self):
        failures = 0
        self.update_status(status="starting", started_at=journal.now_ms())
        while not self.stop.is_set():
            settings = self.settings()
            started = time.monotonic()
            if not settings["enabled"]:
                self.update_status(status="paused")
                self.stop.wait(2)
                continue
            if not all(credentials(journal.ROOT)):
                if any(journal.kv("exchange:" + x + ":snapshot") for x in ("okx", "bybit")):
                    self.process_ai()
                self.client = None
                self.update_status(status="unconfigured", error=None, next_attempt=None)
                self.stop.wait(2)
                continue
            try:
                self.poll()
                self.process_ai()
                failures = 0
            except Exception as e:
                failures += 1
                # Exception strings from arbitrary libraries may contain signed URLs: never persist them.
                self.update_status(status="retrying", error=str(e) if isinstance(e, BinanceError) else "自动采集暂未完成，已有记录保留，正在重试", attempted_at=journal.now_ms())
                if failures >= 2:
                    self.client = None
            interval = min(300, settings["interval"] * 2 ** min(failures, 4))
            self.update_status(next_attempt=journal.now_ms()+int(max(1,interval-(time.monotonic()-started))*1000))
            self.stop.wait(max(1, interval - (time.monotonic() - started)))
        self.update_status(status="stopped")

    def start(self):
        threading.Thread(target=self.run, name="account-recorder", daemon=True).start()
        return self
