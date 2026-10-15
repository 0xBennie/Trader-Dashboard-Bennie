"""Isolated, durable read-only exchange accounts and background collection."""
import json
import os
import tempfile
import threading
import time
from decimal import Decimal
import journal
import recorder
import stop_coverage
from analytics import build_analytics
from exchange_clients import Client, ExchangeError, NAMES, DAY

LOCKS = {exchange: threading.Lock() for exchange in NAMES}
CONFIG_LOCK = threading.Lock()
WORKERS = {}


def config_path():
    return journal.DB.parent / "exchange-accounts.json"


def config():
    with CONFIG_LOCK:
        path = config_path()
        return json.loads(path.read_text()) if path.exists() else {}


def public_config(exchange):
    item = config().get(exchange, {})
    return {"configured": bool(item.get("key") and item.get("secret") and item.get("identity")),
            "enabled": item.get("enabled", True), "checked_at": item.get("checked_at"), "name": NAMES[exchange]}


def write_config(exchange, item):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with CONFIG_LOCK:
        values = json.loads(path.read_text()) if path.exists() else {}
        values[exchange] = item
        fd, temporary = tempfile.mkstemp(prefix=".exchange-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(values, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)


def validate_exchange(exchange):
    if exchange not in NAMES: raise ExchangeError("交易所不支持。")


def save(exchange, payload):
    validate_exchange(exchange)
    if not LOCKS[exchange].acquire(blocking=False):
        raise ExchangeError("该交易所正在同步，完成后再更新连接。")
    try:
        old = config().get(exchange, {})
        fields = ("key", "secret", "passphrase") if exchange == "okx" else ("key", "secret")
        item = dict(old)
        # Rotation is atomic: every credential must belong to the same new key.
        for field in fields:
            value = str(payload.get(field, "")).strip()
            if not 1 <= len(value) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ExchangeError("请完整填写同一组 API 凭证；OKX 还需要创建密钥时的 Passphrase。")
            item[field] = value
        client = Client(exchange, item)
        client.align()
        identity = client.identity()
        bound = journal.kv("exchange:" + exchange + ":identity") or old.get("identity")
        if bound and identity != bound:
            raise ExchangeError("此账本已绑定另一账户。可更新同一账户的密钥，不能把不同账户的交易混入现有账本。")
        item.update(identity=identity, checked_at=journal.now_ms(), enabled=True)
        # A durable binding survives credential-file loss or later rotation.
        with journal.connect() as db:
            journal.set_kv(db, "exchange:" + exchange + ":identity", identity)
        write_config(exchange, item)
    finally:
        LOCKS[exchange].release()
    if exchange in WORKERS: WORKERS[exchange].wake.set()
    return public_config(exchange)


def set_enabled(exchange, enabled):
    validate_exchange(exchange)
    if not isinstance(enabled, bool): raise ExchangeError("开关格式不正确。")
    with LOCKS[exchange]:
        item = config().get(exchange)
        if not item: raise ExchangeError("请先保存 API 凭证。")
        item["enabled"] = enabled
        write_config(exchange, item)
    if exchange in WORKERS: WORKERS[exchange].wake.set()
    return public_config(exchange)


def key(exchange, name):
    return "exchange:" + exchange + ":" + name


def update_status(exchange, **values):
    with journal.connect() as db:
        current = journal.kv(key(exchange, "status"), {}, db)
        current.update(values)
        journal.set_kv(db, key(exchange, "status"), current)


def collect(exchange, days=None):
    validate_exchange(exchange)
    if not LOCKS[exchange].acquire(blocking=False): return False
    try:
        item = config().get(exchange, {})
        if not item.get("identity"): raise ExchangeError("请先配置并验证该交易所的只读 API。")
        if days is not None and days not in {7, 30, 90}: raise ExchangeError("请选择近 7、30 或 90 天。")
        update_status(exchange, status="running", error=None, attempted_at=journal.now_ms())
        client = Client(exchange, item)
        client.align()
        if client.identity() != item["identity"]: raise ExchangeError("账户身份与已绑定账本不匹配，已停止同步。")
        previous = journal.kv(key(exchange, "snapshot"), {})
        end = int(time.time() * 1000) + client.offset
        # Overlap seven days to absorb delayed ledger postings and late order updates.
        start = end - (days or (7 if previous else 30)) * DAY
        if previous and not days:
            start = min(start, previous["coverage"]["end"])
            if end - start > 90 * DAY:
                raise ExchangeError("离线超过 90 天，无法保证自动补齐。请核对导出账单后手动选择同步范围。")
        captured = journal.now_ms()
        result = client.fetch(start, end, journal.kv(key(exchange, "instruments"), {}))
        # The first position read can race with fills. Refuse to publish an inconsistent cut.
        if exchange == "okx":
            latest = client.positions(client.get("/api/v5/account/positions", {"instType": "SWAP"}))
        else:
            latest = client.positions(client.pages("/v5/position/list", {"category": "linear", "settleCoin": "USDT"}, limit=200))
        signature = lambda positions: sorted((p["symbol"], p["positionSide"], Decimal(p["positionAmt"]), Decimal(p["entryPrice"])) for p in positions)
        if signature(latest) != signature(result["positions"]):
            raise ExchangeError("同步期间仓位发生变化，正在补读成交；本轮未提交。")
        commit(exchange, result, start, end, captured, previous)
        return True
    except Exception as exc:
        update_status(exchange, status="retrying", error=str(exc) if isinstance(exc, ExchangeError) else "同步未完成，已有记录保留，稍后自动重试。")
        return False
    finally:
        LOCKS[exchange].release()


def commit(exchange, result, start, end, captured, previous=None):
    previous = previous or {}
    prior = previous.get("coverage", {})
    continuous = not prior or start <= prior.get("end", 0) + 1
    earliest = min(start, prior.get("start", start)) if continuous else start
    coverage = {"start": earliest, "end": end, "days": max(1, round((end - earliest) / DAY)),
                "symbols": sorted({p["symbol"] for p in result["positions"]} | {t["symbol"] for t in result["trades"]}),
                "scope": "USDT 永续", "complete": continuous, "note": "首次同步前的交易和离线期间的仓位快照不会回造。"}
    snapshot = {"captured_at": captured, "positions": result["positions"], "coverage": coverage, "wallet": result["wallet"]}
    contexts = {symbol: {"status": "complete", "captured_at": captured,
                         "positions": [p for p in result["positions"] if p["symbol"] == symbol],
                         "orders": [o for o in result["orders"] if o["symbol"] == symbol],
                         "source": NAMES[exchange], "planning": {},
                         "notes": ["读取普通及条件挂单；移动止损不计入固定止损覆盖。"]}
                for symbol in {p["symbol"] for p in result["positions"]} | {o["symbol"] for o in result["orders"]}}
    if exchange == "bybit":
        sequencing = {}
        for fill in result["trades"]:
            sequencing.setdefault((fill["symbol"], fill["positionSide"], fill["time"], fill.get("sequence", "0")), set()).add(fill["side"])
        if any(len(sides) > 1 for sides in sequencing.values()):
            raise ExchangeError("Bybit 同一时刻存在无法排序的反向成交，暂不重建交易轮次，请核对导出成交。")
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for fill in result["trades"]:
            db.execute("INSERT OR REPLACE INTO exchange_trades VALUES(?,?,?,?,?)", (exchange, fill["symbol"], fill["id"], fill["time"], json.dumps(fill)))
        for bill in result["income"]:
            db.execute("INSERT OR REPLACE INTO exchange_income VALUES(?,?,?,?,?)", (exchange, bill["incomeType"], bill["tranId"], bill["time"], json.dumps(bill)))
        _, issues = recorder.reconcile(db, result["positions"], captured,
                                      queue_ai=journal.kv("recorder_settings", recorder.DEFAULTS, db).get("auto_ai", True), exchange=exchange)
        recorder.capture_positions(db, result["positions"], contexts, captured, exchange=exchange)
        # Preserve original exchange evidence separately from the normalized ledger.
        journal.set_kv(db, key(exchange, "last_raw"), result.get("raw", {}))
        journal.set_kv(db, key(exchange, "snapshot"), snapshot)
        journal.set_kv(db, key(exchange, "contexts"), contexts)
        journal.set_kv(db, key(exchange, "instruments"), result["instruments"])
        journal.set_kv(db, key(exchange, "status"), {"status": "partial" if issues or not continuous else "complete",
            "last_success": captured, "error": "；".join(issues) if issues else None, "issues": issues,
            "history_gap": not continuous, "interval": 60})


def states(db):
    result = {}
    for exchange in NAMES:
        snapshot = journal.kv(key(exchange, "snapshot"), {}, db)
        coverage = snapshot.get("coverage", {})
        start, end = coverage.get("start", 0), coverage.get("end", journal.now_ms())
        fills = [json.loads(r[0]) for r in db.execute("SELECT raw FROM exchange_trades WHERE exchange=? AND time BETWEEN ? AND ? ORDER BY time DESC", (exchange, start, end))]
        income = [json.loads(r[0]) for r in db.execute("SELECT raw FROM exchange_income WHERE exchange=? AND time BETWEEN ? AND ? ORDER BY time", (exchange, start, end))]
        for fill in fills: fill["order_key"] = fill["orderId"]
        totals, curve = journal.aggregate_income(income)
        status = journal.kv(key(exchange, "status"), {}, db)
        result[exchange] = {**public_config(exchange), "snapshot": snapshot, "sync": status, "recorder": status,
            "trades": fills, "orders": journal.group_orders(fills), "totals": totals, "curve": curve,
            "trade_context": journal.kv(key(exchange, "contexts"), {}, db),
            "analytics": build_analytics(income, fills, coverage)}
        result[exchange]["stop_coverage"] = stop_coverage.summarize(result[exchange], journal.now_ms())
    return result


class Worker:
    def __init__(self, exchange):
        self.exchange = exchange
        self.stop = threading.Event()
        self.wake = threading.Event()

    def run(self):
        failures = 0
        while not self.stop.is_set():
            self.wake.clear()
            settings = public_config(self.exchange)
            if not settings["configured"]:
                update_status(self.exchange, status="unconfigured")
                delay = 5
            elif not settings["enabled"]:
                update_status(self.exchange, status="paused")
                delay = 5
            else:
                success = collect(self.exchange)
                failures = 0 if success else failures + 1
                delay = min(300, 60 * 2 ** min(failures, 3))
            self.wake.wait(delay)

    def start(self):
        threading.Thread(target=self.run, name=self.exchange + "-recorder", daemon=True).start()
        return self


def start_workers():
    for exchange in NAMES:
        WORKERS[exchange] = Worker(exchange).start()
    return list(WORKERS.values())


def request_sync(exchange, days=30):
    validate_exchange(exchange)
    if type(days) is not int or days not in {7, 30, 90}: raise ExchangeError("请选择近 7、30 或 90 天。")
    if not public_config(exchange)["configured"]: raise ExchangeError("请先保存并验证只读 API 凭证。")
    if LOCKS[exchange].locked(): raise ExchangeError("该交易所正在同步，请等待完成。")
    threading.Thread(target=collect, args=(exchange, days), daemon=True).start()
    return {"status": "started", "exchange": exchange}
