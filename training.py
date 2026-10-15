"""Durable pre-trade training. Local permits are plans, never exchange orders."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import journal

HK = ZoneInfo("Asia/Hong_Kong")
D = Decimal
LABELS = {"draft": "草稿", "reviewing": "AI 审核中", "needs_info": "待补充", "rejected": "不通过",
          "review_failed": "审核失败", "cooldown": "冷静期", "awaiting_confirmation": "待本人确认",
          "permitted": "有效计划", "matched": "已关联成交", "expired": "已过期", "cancelled": "已取消",
          "invalidated": "审核已失效"}
AUDIT_LABELS = {"historical": "历史未留预审", "matched": "已关联事前审核", "unreviewed": "违规交易", "pending_match": "待核对"}
PLAN_FIELDS = ("symbol", "market", "direction", "entry_price", "quantity", "stop_loss", "take_profit", "thesis",
               "invalidation", "emotion", "strategy_id", "strategy_version", "backtest_id", "rules_confirmed", "image", "policy_version")


class Conflict(ValueError):
    pass


def now_ms():
    return journal.now_ms()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def day_bounds(stamp):
    dt = datetime.fromtimestamp(stamp / 1000, HK)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.date().isoformat(), int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def number(value, name="数字", positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(name + "格式不正确。")
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(name + "格式不正确。") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(name + "必须是有限" + ("正数。" if positive else "数字。"))
    if result.adjusted() > 24 or result.as_tuple().exponent < -24:
        raise ValueError(name + "超出支持的精度或范围。")
    return result


def integer(value, name, minimum=0, maximum=1000000):
    n = number(value, name)
    if n != n.to_integral() or not minimum <= n <= maximum:
        raise ValueError(name + "超出支持范围。")
    return int(n)


def init():
    """Call once at service startup; an interrupted AI job cannot become approval."""
    with journal.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS training_policies(version INTEGER PRIMARY KEY, effective_at INTEGER, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_intents(id TEXT PRIMARY KEY, version INTEGER, status TEXT, created INTEGER, updated INTEGER, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_intent_history(id INTEGER PRIMARY KEY, intent_id TEXT, version INTEGER, time INTEGER, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_permits(id TEXT PRIMARY KEY, intent_id TEXT, issued INTEGER, expires INTEGER, consumed INTEGER, revoked INTEGER, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_confirmations(key TEXT PRIMARY KEY, intent_id TEXT, permit_id TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS training_audits(id TEXT PRIMARY KEY, first_fill INTEGER, status TEXT, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_tasks(id TEXT PRIMARY KEY, week TEXT UNIQUE, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_activities(id TEXT PRIMARY KEY, day TEXT, kind TEXT, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS training_permits_intent ON training_permits(intent_id);
        ''')
        stamp = now_ms()
        for row in db.execute("SELECT data FROM training_intents WHERE status='reviewing'").fetchall():
            item = json.loads(row[0])
            item.update(status="review_failed", review_token=None, blockers=[block("review_interrupted", "审核因服务重启中断，请重新审核。")])
            _write(db, item, stamp)
        _refresh(db, stamp)


def _policy(db):
    row = db.execute("SELECT data FROM training_policies ORDER BY version DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else {"configured": False, "version": 0, "confirmed": False, "enabled_at": None,
        "updated_at": None, "max_trade_risk_usdt": None, "max_daily_loss_usdt": None, "max_entries_per_day": None,
        "cooldown_seconds": None, "permit_seconds": 300, "price_tolerance_pct": "0.2"}


def get_policy():
    with journal.connect() as db:
        return _policy(db)


def _expect(payload, current):
    if "version" not in payload or integer(payload["version"], "版本") != current:
        raise Conflict("版本已改变，请刷新后核对；未覆盖最新内容。")


def save_policy(payload):
    if payload.get("confirmed") is not True:
        raise ValueError("风险政策必须由本人明确确认。")
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _policy(db)
        _expect(payload, previous["version"])
        p = dict(previous)
        for key in ("max_trade_risk_usdt", "max_daily_loss_usdt"):
            p[key] = str(number(payload.get(key, previous[key]), key, True))
        if D(p["max_trade_risk_usdt"]) > D(p["max_daily_loss_usdt"]):
            raise ValueError("单笔最大风险不能大于当日最大亏损。")
        p["max_entries_per_day"] = integer(payload.get("max_entries_per_day", previous["max_entries_per_day"]), "每日新增风险次数", 1, 10000)
        p["cooldown_seconds"] = integer(payload.get("cooldown_seconds", previous["cooldown_seconds"]), "冷静秒数", 1, 604800)
        p["permit_seconds"] = integer(payload.get("permit_seconds", previous["permit_seconds"]), "计划有效秒数", 1, 3600)
        tolerance = number(payload.get("price_tolerance_pct", previous["price_tolerance_pct"]), "价格容差")
        if not 0 <= tolerance <= 10:
            raise ValueError("价格容差须在 0 到 10% 之间。")
        p["price_tolerance_pct"] = str(tolerance)
        stamp = now_ms()
        p.update(configured=True, confirmed=True, version=previous["version"] + 1,
                 enabled_at=previous["enabled_at"] or stamp, updated_at=stamp)
        db.execute("INSERT INTO training_policies VALUES(?,?,?)", (p["version"], stamp, encoded(p)))
        for row in db.execute("SELECT data FROM training_intents WHERE status NOT IN ('matched','cancelled','expired')").fetchall():
            item = json.loads(row[0])
            _revoke(db, item, stamp)
            item.update(status="invalidated", review_token=None, blockers=[block("policy_changed", "风险政策已改变，请重新保存并审核计划。")])
            _write(db, item, stamp)
        return p


def block(code, message):
    return {"code": code, "message": message}


def _load(db, iid):
    row = db.execute("SELECT data FROM training_intents WHERE id=?", (iid,)).fetchone()
    if not row:
        raise ValueError("找不到这笔计划。")
    return json.loads(row[0])


def _write(db, item, stamp, new=False):
    item["version"] = 1 if new else item["version"] + 1
    item["updated"] = stamp
    item["status_label"] = LABELS[item["status"]]
    data = encoded(item)
    db.execute("INSERT OR REPLACE INTO training_intents VALUES(?,?,?,?,?,?)",
               (item["id"], item["version"], item["status"], item["created"], stamp, data))
    db.execute("INSERT INTO training_intent_history(intent_id,version,time,data) VALUES(?,?,?,?)", (item["id"], item["version"], stamp, data))
    return item


def _revoke(db, item, stamp):
    if item.get("permit") and item["permit"].get("consumed_at") is None:
        permit = item["permit"]
        permit["revoked_at"] = permit.get("revoked_at") or stamp
        db.execute("UPDATE training_permits SET revoked=?,data=? WHERE id=?", (permit["revoked_at"], encoded(permit), permit["id"]))


def _refresh(db, stamp):
    for row in db.execute("SELECT data FROM training_intents WHERE status IN ('permitted','cooldown')").fetchall():
        item = json.loads(row[0])
        if item["status"] == "permitted" and item["permit"]["expires_at"] <= stamp:
            item.update(status="expired", blockers=[block("permit_expired", "有效计划已过期，请重新审核。")])
            _write(db, item, stamp)
        elif item["status"] == "cooldown" and (item.get("cooldown_until") or 0) <= stamp:
            item.update(status="awaiting_confirmation", blockers=[])
            _write(db, item, stamp)


def _risk(item):
    empty = {"valid": False, "risk_usdt": None, "reward_usdt": None, "rr": None, "reason": "请补全方向、入场价、数量和有效止损。"}
    try:
        entry, qty, stop = [number(item.get(k), k, True) for k in ("entry_price", "quantity", "stop_loss")]
        sign = 1 if item.get("direction") == "LONG" else -1 if item.get("direction") == "SHORT" else 0
        risk = (entry - stop) * sign * qty
        if not sign or risk <= 0:
            return dict(empty, reason="止损必须在方向对应的亏损侧。")
        reward = None
        if item.get("take_profit"):
            reward = (number(item["take_profit"], "目标", True) - entry) * sign * qty
            if reward <= 0:
                return dict(empty, reason="目标必须在方向对应的盈利侧。")
        return {"valid": True, "risk_usdt": str(risk), "reward_usdt": str(reward) if reward is not None else None,
                "rr": str(reward / risk) if reward is not None else None, "reason": "计划名义止损风险；跳空、手续费和滑点可能增加实际损失。"}
    except ValueError:
        return empty


def save_intent(payload):
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_ms()
        _refresh(db, stamp)
        item = _load(db, payload["id"]) if payload.get("id") else None
        _expect(payload, item["version"] if item else 0)
        if item and item["status"] == "matched":
            raise Conflict("已关联成交的计划不可改写，请新建另一笔计划。")
        previous = dict(item or {})
        if item:
            _revoke(db, item, stamp)
        else:
            item = {"id": uuid.uuid4().hex, "created": stamp, "version": 0}
        symbol = str(payload.get("symbol", item.get("symbol", ""))).strip().upper()
        if symbol and not re.fullmatch(r"[A-Z0-9]{1,20}USDT", symbol):
            raise ValueError("当前只支持 Binance USDⓈ-M 的 USDT 合约交易对。")
        direction = payload.get("direction", item.get("direction", ""))
        if direction not in ("", "LONG", "SHORT"):
            raise ValueError("方向须为 LONG 或 SHORT。")
        emotion = payload.get("emotion", item.get("emotion", "uncertain"))
        if emotion not in ("calm", "fomo", "revenge", "uncertain"):
            raise ValueError("情绪值不正确。")
        item.update(symbol=symbol, direction=direction, emotion=emotion, market="Binance USDⓈ-M 永续")
        for key in ("entry_price", "quantity", "stop_loss", "take_profit"):
            value = payload.get(key, item.get(key, ""))
            item[key] = str(number(value, key, True)) if value not in (None, "") else ""
        for key in ("thesis", "invalidation", "strategy_id", "strategy_version", "backtest_id", "image"):
            value = payload.get(key, item.get(key, ""))
            if not isinstance(value, str) or len(value) > (4000 if key in ("thesis", "invalidation") else 512):
                raise ValueError(key + "格式或长度不正确。")
            item[key] = value.strip()
        if item["image"] and (not item["image"].startswith("/assets/") or ".." in item["image"] or "?" in item["image"] or "#" in item["image"]):
            raise ValueError("图片只能引用本站已保存的素材。")
        confirmed = payload.get("rules_confirmed", item.get("rules_confirmed", False))
        if not isinstance(confirmed, bool):
            raise ValueError("规则确认必须为布尔值。")
        # Retired experiments are never carried into a newly saved plan.
        # The previous version remains in the audit trail; save invalidates approval.
        item.update(strategy_id="", strategy_version="", backtest_id="", rules_confirmed=False)
        p = _policy(db)
        item["policy_version"] = p["version"]
        item["plan_hash"] = digest({k: item[k] for k in PLAN_FIELDS})
        cooldown = max(previous.get("cooldown_until") or 0, journal.kv("training_cooldown_until", 0, db))
        if emotion in ("fomo", "revenge") and p["configured"] and (not previous.get("cooldown_until") or previous.get("emotion") not in ("fomo", "revenge")):
            cooldown = max(cooldown, stamp + p["cooldown_seconds"] * 1000)
            journal.set_kv(db, "training_cooldown_until", cooldown)
        item.update(status="draft", risk=_risk(item), blockers=[], review=None, review_token=None, permit=None,
                    cooldown_until=cooldown or None)
        return _write(db, item, stamp, new=not bool(previous))


def get_intent(iid):
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _refresh(db, now_ms())
        return _load(db, iid)


def list_intents():
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _refresh(db, now_ms())
        return [json.loads(row[0]) for row in db.execute("SELECT data FROM training_intents ORDER BY updated DESC LIMIT 100")]


def _fresh(obj, stamp, seconds):
    captured = obj.get("captured_at") if isinstance(obj, dict) else None
    return isinstance(captured, (int, float)) and not isinstance(captured, bool) and 0 <= stamp - captured <= seconds * 1000


def _hard_checks(db, item, evidence, stamp):
    p = _policy(db)
    problems = []
    if not p["configured"]:
        problems.append(block("policy_unconfigured", "请先本人确认风险政策。"))
    if item["policy_version"] != p["version"]:
        problems.append(block("policy_changed", "政策版本已改变，请重新保存并审核。"))
    for field, label in (("symbol", "交易对"), ("direction", "方向"), ("thesis", "入场理由"), ("invalidation", "失效条件")):
        if not item.get(field):
            problems.append(block("missing_" + field, "请补充" + label + "。"))
    risk = _risk(item)
    if not risk["valid"]:
        problems.append(block("invalid_stop_risk", risk["reason"]))
    if item.get("strategy_id") or item.get("backtest_id"):
        problems.append(block("experiment_retired", "旧计划关联的策略实验已移除，请重新保存并审核计划。"))
    evidence = evidence if isinstance(evidence, dict) else {}
    if evidence.get("errors"):
        problems.append(block("evidence_error", "证据读取失败，请重新获取行情与账户资料。"))
    if not _fresh(evidence, stamp, 60):
        problems.append(block("evidence_stale", "证据包缺失或已过期。"))
    for key, seconds, label in (("quote", 30, "行情"), ("positions", 60, "仓位"), ("orders", 90, "挂单"), ("account", 60, "账户损益"), ("ledger", 60, "当日成交账本")):
        obj = evidence.get(key)
        if not _fresh(obj, stamp, seconds):
            problems.append(block(key + "_stale", label + "缺失或已过期，请刷新。"))
        if key in ("positions", "orders") and (not isinstance(obj, dict) or not isinstance(obj.get("rows"), list)):
            problems.append(block(key + "_incomplete", label + "数据不完整。"))
        if key in ("orders", "account", "ledger") and (not isinstance(obj, dict) or obj.get("complete") is not True):
            problems.append(block(key + "_incomplete", label + "未完整读取。"))
    ledger = evidence.get("ledger")
    if (not isinstance(ledger, dict) or ledger.get("day_start") != day_bounds(stamp)[1]
            or not _fresh({"captured_at": ledger.get("through")}, stamp, 60)):
        problems.append(block("ledger_coverage_incomplete", "缺少截至最近一分钟完整覆盖香港当日的成交账本，不能确认新增风险次数。"))
    quote = evidence.get("quote") or {}
    reference = None
    try:
        reference = number(quote.get("price"), "行情价格", True)
        if quote.get("symbol") != item["symbol"]:
            raise ValueError()
    except ValueError:
        problems.append(block("quote_invalid", "行情价格或交易对不匹配。"))
    account = evidence.get("account") or {}
    daily = None
    try:
        daily = number(account.get("daily_realized_pnl_usdt"), "当日已实现损益")
        if account.get("day_start") != day_bounds(stamp)[1]:
            raise ValueError()
    except ValueError:
        problems.append(block("daily_pnl_missing", "缺少完整的香港当日已实现损益；不能假定为零。"))
    if daily is not None and account.get("complete") is True and _fresh(account, stamp, 60) and account.get("day_start") == day_bounds(stamp)[1]:
        journal.set_kv(db, "training_last_account", account)
    if p["configured"] and risk["valid"]:
        entry, stop = D(item["entry_price"]), D(item["stop_loss"])
        tolerance = D(p["price_tolerance_pct"]) / 100
        lower, upper = entry * (1 - tolerance), entry * (1 + tolerance)
        sign = 1 if item["direction"] == "LONG" else -1
        if reference is not None and (reference - stop) * sign <= 0:
            problems.append(block("quote_crossed_stop", "最新价格已到达或越过止损，原计划已经失效，请修改后重审。"))
        if (lower - stop) * sign <= 0 or (upper - stop) * sign <= 0:
            problems.append(block("permit_crosses_stop", "许可价格区间触及或越过止损，请本人收窄价格容差或重新设置计划后重审。"))
        if item.get("take_profit"):
            target = D(item["take_profit"])
            if reference is not None and (target - reference) * sign <= 0:
                problems.append(block("quote_crossed_target", "最新价格已到达或越过目标，原入场计划不再成立，请修改后重审。"))
            if (target - lower) * sign <= 0 or (target - upper) * sign <= 0:
                problems.append(block("permit_crosses_target", "许可价格区间触及或越过目标，请本人收窄价格容差或重新设置计划后重审。"))
        nominal = D(risk["risk_usdt"])
        worst = nominal + D(item["entry_price"]) * D(item["quantity"]) * D(p["price_tolerance_pct"]) / 100
        if worst > D(p["max_trade_risk_usdt"]):
            problems.append(block("trade_risk_exceeded", "包含价格容差的名义止损风险超过单笔上限。"))
        if reference is not None and abs(reference / D(item["entry_price"]) - 1) * 100 > D(p["price_tolerance_pct"]):
            problems.append(block("price_moved", "最新价格已超出计划价格容差，请修改计划后重审。"))
        day_start = day_bounds(stamp)[1]
        day_audits = [json.loads(r[0]) for r in db.execute("SELECT data FROM training_audits WHERE first_fill>=? AND status!='historical'", (day_start,))]
        entries = sum(a.get("risk_classification") == "new_risk" for a in day_audits)
        if any(a.get("risk_classification") == "unknown" for a in day_audits):
            problems.append(block("risk_orders_unresolved", "当日单向成交链尚未对齐，无法确定新增风险次数。"))
        reserved_count, reserved_risk = 0, D(0)
        for row in db.execute("SELECT data FROM training_permits WHERE consumed IS NULL AND revoked IS NULL AND expires>? AND intent_id!=?", (stamp, item["id"])):
            permit = json.loads(row[0])
            reserved_count += 1
            reserved_risk += D(permit["risk_usdt"])
        if entries + reserved_count >= p["max_entries_per_day"]:
            problems.append(block("entry_limit", "当日新增风险订单及待使用计划已达到次数上限。"))
        if daily is not None and max(D(0), -daily) + reserved_risk + worst > D(p["max_daily_loss_usdt"]):
            problems.append(block("daily_risk_exceeded", "当日已实现亏损与待使用计划风险已超出日内预算。"))
    return problems


def _evidence_snapshot(evidence):
    """Persist only review evidence fields, never arbitrary transport/client objects."""
    if not isinstance(evidence, dict):
        return {}
    return json.loads(encoded({k: evidence[k] for k in ("captured_at", "quote", "positions", "orders", "account", "ledger", "errors", "backtest") if k in evidence}))


def _material_value(key, value):
    numeric = {"positionAmt", "entryPrice", "liquidationPrice", "leverage", "isolatedWallet", "breakEvenPrice",
               "price", "quantity", "origQty", "executedQty", "triggerPrice", "stopPrice", "activatePrice",
               "callbackRate", "daily_realized_pnl_usdt"}
    if key in numeric and value is not None:
        try:
            n = number(value)
            return "0" if not n else format(n.normalize(), "f")
        except ValueError:
            return value
    return value


def _material_rows(evidence, section):
    """Ignore prices/PnL that continuously fluctuate, but compare actual exposure and orders."""
    obj = evidence.get(section) if isinstance(evidence, dict) else None
    rows = obj.get("rows") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    fields = ("symbol", "positionSide", "positionAmt", "entryPrice", "liquidationPrice", "leverage", "marginType", "isolatedWallet", "breakEvenPrice") if section == "positions" else (
        "symbol", "positionSide", "side", "type", "orderType", "price", "quantity", "origQty", "executedQty",
        "triggerPrice", "stopPrice", "activatePrice", "callbackRate", "reduceOnly", "closePosition", "status",
        "algoStatus", "workingType", "priceProtect", "timeInForce")
    return sorted(encoded({key: _material_value(key, row[key]) for key in fields if key in row}) for row in rows)


def _material_changes(review, evidence):
    original = review.get("input_evidence") or review.get("evidence")
    if not isinstance(original, dict) or not isinstance(evidence, dict):
        return [block("review_evidence_missing", "缺少 AI 实际审核的原始证据，需重新审核。")]
    changes = []
    for section, label in (("positions", "仓位数量、方向或成本"), ("orders", "挂单价格、数量或保护条件")):
        if _material_rows(original, section) != _material_rows(evidence, section):
            changes.append(block(section + "_changed", label + "在 AI 审核后发生变化，必须重新审核当前账户状态。"))
    original_account, account = original.get("account") or {}, evidence.get("account") or {}
    if isinstance(original_account, dict) and isinstance(account, dict):
        keys = ("daily_realized_pnl_usdt", "day_start")
        if any(_material_value(key, original_account.get(key)) != _material_value(key, account.get(key)) for key in keys):
            changes.append(block("account_changed", "当日已实现损益或日期在 AI 审核后发生变化，必须重新审核。"))
    else:
        changes.append(block("account_changed", "账户损益证据发生变化，必须重新审核。"))
    original_ledger, ledger = original.get("ledger") or {}, evidence.get("ledger") or {}
    if (isinstance(original_ledger, dict) and isinstance(ledger, dict) and "order_count" in original_ledger and "order_count" in ledger
            and original_ledger["order_count"] != ledger["order_count"]):
        changes.append(block("ledger_changed", "当日成交订单数在 AI 审核后发生变化，必须重新审核。"))
    return changes


def begin_review(iid, version, evidence):
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_ms()
        _refresh(db, stamp)
        reconcile_audit(db, stamp)
        item = _load(db, iid)
        _expect({"version": version}, item["version"])
        if item["status"] in ("permitted", "matched", "cancelled"):
            raise Conflict("当前状态不能发起审核，请新建或编辑计划。")
        if item["status"] == "reviewing":
            raise Conflict("该计划正在审核，请等待或修改后重新发起。")
        problems = _hard_checks(db, item, evidence, stamp)
        cooldown = max(item.get("cooldown_until") or 0, journal.kv("training_cooldown_until", 0, db))
        item.update(blockers=problems, cooldown_until=cooldown or None, review_token=None)
        if problems:
            item["status"] = "needs_info"
        else:
            item.update(status="reviewing", review_token=uuid.uuid4().hex,
                        review={"started_at": stamp, "completed_at": None, "plan_hash": item["plan_hash"],
                                "policy_version": item["policy_version"], "evidence": _evidence_snapshot(evidence),
                                "input_evidence": _evidence_snapshot(evidence), "completion_evidence": None,
                                "confirmation_evidence": None, "requires_review": False, "ai": None})
        return _write(db, item, stamp)


def _ai_valid(ai):
    if not isinstance(ai, dict) or ai.get("verdict") not in ("support", "needs_info", "reject"):
        return False
    if not all(isinstance(ai.get(k), str) and ai[k].strip() for k in ("summary", "invalidation", "next_step")):
        return False
    if not all(isinstance(ai.get(k), list) and all(isinstance(v, str) for v in ai[k]) for k in ("supporting", "opposing", "unknowns")):
        return False
    return not ai.get("error") and (ai["verdict"] != "support" or bool(ai["supporting"]))


def complete_review(iid, review_token, ai_result, evidence):
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_ms()
        reconcile_audit(db, stamp)
        item = _load(db, iid)
        review = item.get("review") or {}
        if (not review_token or item["status"] != "reviewing" or item.get("review_token") != review_token
                or review.get("plan_hash") != item["plan_hash"] or review.get("policy_version") != _policy(db)["version"]):
            raise Conflict("审核任务已失效，旧结果未写入当前计划。")
        problems = _hard_checks(db, item, evidence, stamp)
        changes = _material_changes(review, evidence)
        problems.extend(changes)
        review.update(completed_at=stamp, completion_evidence=_evidence_snapshot(evidence),
                      requires_review=bool(changes), ai=ai_result if _ai_valid(ai_result) else None)
        item.update(review_token=None, blockers=problems)
        if not _ai_valid(ai_result):
            item["status"] = "review_failed"
            item["blockers"].append(block("ai_failed", "AI 未返回完整可验证的审核结果，请重新审核。"))
        elif problems:
            item["status"] = "needs_info"
        elif ai_result["verdict"] == "reject":
            item.update(status="rejected", blockers=[block("ai_rejected", ai_result["next_step"])])
        elif ai_result["verdict"] == "needs_info":
            item.update(status="needs_info", blockers=[block("ai_needs_info", ai_result["next_step"])])
        elif (item.get("cooldown_until") or 0) > stamp:
            item.update(status="cooldown", blockers=[block("cooldown_active", "冷静期结束后，仍需本人重新确认原计划。")])
        else:
            item["status"] = "awaiting_confirmation"
        return _write(db, item, stamp)


def confirm_intent(iid, payload, evidence):
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not 8 <= len(key) <= 200 or not key.strip():
        raise ValueError("确认需要 8–200 字符的唯一幂等键。")
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_ms()
        _refresh(db, stamp)
        reconcile_audit(db, stamp)
        old = db.execute("SELECT intent_id,permit_id FROM training_confirmations WHERE key=?", (key,)).fetchone()
        item = _load(db, iid)
        if old:
            if old["intent_id"] != iid:
                raise Conflict("该确认键已用于另一笔计划。")
            if not item.get("permit") or item["permit"]["id"] != old["permit_id"]:
                raise Conflict("该确认键属于计划的旧版本，不能用于当前版本。")
            return item
        _expect(payload, item["version"])
        if item["status"] not in ("awaiting_confirmation", "cooldown"):
            raise Conflict("只有完整审核通过且尚未确认的计划可以确认。")
        review = item.get("review") or {}
        if (not _ai_valid(review.get("ai")) or review["ai"]["verdict"] != "support" or review.get("plan_hash") != item["plan_hash"]
                or review.get("policy_version") != _policy(db)["version"]):
            raise Conflict("计划、策略或政策已变化，请重新审核。")
        problems = _hard_checks(db, item, evidence, stamp)
        changes = _material_changes(review, evidence)
        problems.extend(changes)
        review["confirmation_evidence"] = _evidence_snapshot(evidence)
        review["requires_review"] = review.get("requires_review", False) or bool(changes)
        if review["requires_review"] and not changes:
            problems.append(block("review_evidence_changed", "账户材料曾在本次审核后发生实质变化，需重新审核。"))
        cooldown = max(item.get("cooldown_until") or 0, journal.kv("training_cooldown_until", 0, db))
        if cooldown > stamp:
            problems.append(block("cooldown_active", "冷静期尚未结束，不能生成有效计划。"))
        if problems:
            item.update(blockers=problems, status="cooldown" if all(p["code"] == "cooldown_active" for p in problems) else "needs_info",
                        cooldown_until=cooldown or None)
            return _write(db, item, stamp)
        p = _policy(db)
        entry = D(item["entry_price"])
        tolerance = D(p["price_tolerance_pct"]) / 100
        permit = {"id": uuid.uuid4().hex, "intent_id": iid, "plan_hash": item["plan_hash"], "policy_version": p["version"],
                  "symbol": item["symbol"], "direction": item["direction"], "issued_at": stamp,
                  "expires_at": stamp + p["permit_seconds"] * 1000, "reference_price": str(evidence["quote"]["price"]),
                  "price_min": str(entry * (1 - tolerance)), "price_max": str(entry * (1 + tolerance)), "quantity": item["quantity"],
                  "risk_usdt": str(D(item["risk"]["risk_usdt"]) + entry * D(item["quantity"]) * tolerance),
                  "consumed_at": None, "order_key": None, "audit_id": None, "revoked_at": None}
        db.execute("INSERT INTO training_permits VALUES(?,?,?,?,?,?,?)", (permit["id"], iid, stamp, permit["expires_at"], None, None, encoded(permit)))
        db.execute("INSERT INTO training_confirmations VALUES(?,?,?,?)", (key, iid, permit["id"], stamp))
        item.update(status="permitted", permit=permit, blockers=[])
        journal.set_kv(db, "training_last_account", evidence["account"])
        return _write(db, item, stamp)


def cancel_intent(iid, payload):
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        item = _load(db, iid)
        _expect(payload, item["version"])
        if item["status"] == "matched":
            raise Conflict("实际成交的历史计划不能取消。")
        if item["status"] == "cancelled":
            return item
        stamp = now_ms()
        _revoke(db, item, stamp)
        item.update(status="cancelled", review_token=None, blockers=[])
        return _write(db, item, stamp)


def _risk_orders(db):
    """Prefer recorder's reconciled risk allocations; ambiguous one-way rows stay unknown."""
    groups, known = {}, set()
    records = {j["id"]: j for j in journal.binance_journals(db)}
    for row in db.execute("SELECT * FROM trade_events WHERE kind IN ('open','add','reduce','close') ORDER BY occurred,id"):
        j = records.get(row["journal_id"])
        if not j:
            continue
        event = json.loads(row["data"])
        order_key = str(event.get("order_key", ""))
        side = (j.get("execution") or {}).get("positionSide", "BOTH")
        known.add((j["symbol"], order_key, side))
        if row["kind"] not in ("open", "add"):
            continue
        key = (j["symbol"], order_key, side, j["direction"])
        group = groups.setdefault(key, {"parts": [], "journal_ids": set(), "kinds": set(), "risk_classification": "new_risk"})
        group["parts"].append({"time": row["occurred"], "quantity": event["quantity"], "price": event["price"]})
        group["journal_ids"].add(j["id"])
        group["kinds"].add(row["kind"])
    # Trades may arrive before a full ledger/position alignment. Do not silently approve them.
    for row in db.execute("SELECT raw FROM trades ORDER BY time,id"):
        fill = json.loads(row[0])
        symbol, side = fill.get("symbol"), fill.get("positionSide", "BOTH")
        order_key = str(fill.get("orderId", "fill:" + str(fill.get("id"))))
        if not symbol or (symbol, order_key, side) in known or not all(k in fill for k in ("qty", "price", "side", "time")):
            continue
        if side in ("LONG", "SHORT"):
            if (side == "LONG" and fill["side"] != "BUY") or (side == "SHORT" and fill["side"] != "SELL"):
                continue
            direction, classification = side, "new_risk"
        else:
            direction, classification = ("LONG" if fill["side"] == "BUY" else "SHORT"), "unknown"
        key = (symbol, order_key, side, direction)
        group = groups.setdefault(key, {"parts": [], "journal_ids": set(), "kinds": {"open"}, "risk_classification": classification})
        group["parts"].append({"time": int(fill["time"]), "quantity": fill["qty"], "price": fill["price"]})
    return groups


def _within(permit, parts):
    return all(permit["issued_at"] < part["time"] < permit["expires_at"]
               and (permit.get("revoked_at") is None or part["time"] < permit["revoked_at"])
               and D(permit["price_min"]) <= D(part["price"]) <= D(permit["price_max"]) for part in parts)


def reconcile_audit(db, stamp):
    """Use the caller's transaction; no network, commits, or retroactive approvals."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_audits'").fetchone():
        return []
    policy = _policy(db)
    permits = [json.loads(row[0]) for row in db.execute("SELECT data FROM training_permits ORDER BY issued,id")]
    audits = []
    seen = set()
    for (symbol, order_key, side, direction), group in _risk_orders(db).items():
        parts = group["parts"]
        qty = sum((number(p["quantity"], "成交数量", True) for p in parts), D(0))
        first, last = min(p["time"] for p in parts), max(p["time"] for p in parts)
        aid = digest([symbol, order_key, side, direction])[:32]
        seen.add(aid)
        audit = {"id": aid, "symbol": symbol, "direction": direction, "position_side": side, "order_key": order_key,
                 "first_fill_at": first, "last_fill_at": last, "quantity": str(qty),
                 "price": str(sum((D(p["quantity"]) * D(p["price"]) for p in parts), D(0)) / qty),
                 "kind": next(iter(group["kinds"])) if len(group["kinds"]) == 1 else "mixed",
                 "risk_classification": group["risk_classification"], "status": "unreviewed", "intent_id": None,
                 "permit_id": None, "journal_ids": sorted(group["journal_ids"]), "reason": "本次开仓或加仓没有匹配在成交前完成 AI 审核并由本人确认的有效计划。", "updated": stamp,
                 "candidate_intent_ids": [], "deviation_fields": []}
        historical = policy["enabled_at"] is None or first < policy["enabled_at"]
        if historical:
            audit.update(status="historical", reason="发生于风险政策启用前，历史未留预审，不追溯判断违纪。")
        elif group["risk_classification"] == "unknown":
            audit.update(status="pending_match", reason="单向持仓的完整成交链尚未对齐，不能确定该订单是否新增风险。")
        else:
            prior = [p for p in permits if p["symbol"] == symbol and p["direction"] == direction
                     and p["issued_at"] < first < p["expires_at"]
                     and (p.get("revoked_at") is None or first < p["revoked_at"])
                     and p.get("audit_id") in (None, aid)]
            audit["candidate_intent_ids"] = sorted({p["intent_id"] for p in prior})
            if len(prior) == 1:
                candidate = prior[0]
                if D(candidate["quantity"]) != qty:
                    audit["deviation_fields"].append("quantity")
                if any(not D(candidate["price_min"]) <= D(part["price"]) <= D(candidate["price_max"]) for part in parts):
                    audit["deviation_fields"].append("price")
                if any(part["time"] >= candidate["expires_at"] or (candidate.get("revoked_at") is not None and part["time"] >= candidate["revoked_at"]) for part in parts):
                    audit["deviation_fields"].append("time")
            eligible = [p for p in permits if p["symbol"] == symbol and p["direction"] == direction and _within(p, parts)
                        and (p.get("audit_id") in (None, aid))]
            exact = [p for p in eligible if D(p["quantity"]) == qty]
            # Two possible permits are genuinely ambiguous; chronological proximity is not proof.
            if len(exact) == 1:
                permit = exact[0]
                audit.update(status="matched", intent_id=permit["intent_id"], permit_id=permit["id"], reason="交易对、方向、全部成交时间/价格及新增风险数量严格匹配。")
                if permit.get("consumed_at") is None:
                    permit.update(consumed_at=last, order_key=order_key, audit_id=aid)
                    db.execute("UPDATE training_permits SET consumed=?,data=? WHERE id=?", (last, encoded(permit), permit["id"]))
                    item = _load(db, permit["intent_id"])
                    if item.get("permit") and item["permit"]["id"] == permit["id"]:
                        item.update(status="matched", permit=permit, blockers=[])
                        _write(db, item, stamp)
            elif len(exact) > 1 or any(qty < D(p["quantity"]) for p in eligible):
                audit.update(status="pending_match", reason="存在多个候选或只成交部分数量，不能确认已通过预审。")
            # If later partial fills invalidate a former match, do not keep a false approval.
            consumed = next((p for p in permits if p.get("audit_id") == aid), None)
            if consumed and audit["status"] != "matched":
                audit.update(status="unreviewed", intent_id=consumed["intent_id"], permit_id=consumed["id"],
                             reason="后续成交数量、时间或价格超出原许可；已消费的许可不会再次使用。")
                item = _load(db, consumed["intent_id"])
                if item.get("permit") and item["permit"]["id"] == consumed["id"] and item["status"] == "matched":
                    item.update(status="invalidated", blockers=[block("fill_mismatch", audit["reason"])])
                    _write(db, item, stamp)
        audit["status_label"] = AUDIT_LABELS[audit["status"]]
        db.execute("INSERT OR REPLACE INTO training_audits VALUES(?,?,?,?)", (aid, first, audit["status"], encoded(audit)))
        audits.append(audit)
    # Unknown one-way candidates can disappear when the recorder proves they were exits.
    for row in db.execute("SELECT id,data FROM training_audits").fetchall():
        if row["id"] not in seen and json.loads(row["data"]).get("risk_classification") == "unknown":
            db.execute("DELETE FROM training_audits WHERE id=?", (row["id"],))
    return sorted(audits, key=lambda a: a["first_fill_at"], reverse=True)


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "ratio": str(D(numerator) / denominator) if denominator else None}


def _metrics(db, audits, policy):
    active = [a for a in audits if a["status"] != "historical" and a.get("risk_classification") == "new_risk"]
    matched = sum(a["status"] == "matched" for a in active)
    unreviewed = sum(a["status"] == "unreviewed" for a in active)
    pending = sum(a["status"] == "pending_match" for a in active)
    comparable = [a for a in active if a["status"] == "matched" or (a["status"] == "unreviewed" and len(a.get("candidate_intent_ids", [])) == 1)]
    excluded = sum(a["status"] == "pending_match" and bool(a.get("candidate_intent_ids")) for a in active)
    cohorts = {k: {"closed_cycles": 0, "net_usdt": None, "non_usdt_fees": {}} for k in ("planned", "unreviewed", "unclassified")}
    completed = personal_completed = ai_completed = due = 0
    records = journal.all_journals(db)
    reviews = journal.review_progress(db, records, now_ms())
    for item in records:
        execution = item.get("execution") or {}
        closed = execution.get("closed_ms")
        if not closed or not policy["enabled_at"] or closed < policy["enabled_at"]:
            continue
        due += 1
        report = db.execute("SELECT 1 FROM analyses WHERE journal_id=? AND status='complete' AND created>=? LIMIT 1", (item["id"], closed)).fetchone()
        personal = item["review_status"]["personal_complete"]
        personal_completed += int(personal)
        ai_completed += int(bool(report))
        if personal or report:
            completed += 1
        if not journal.is_binance(item):
            continue  # Pre-trade permits and audit cohorts currently belong to Binance only.
        related = [a for a in audits if item["id"] in a["journal_ids"]]
        key = "unclassified"
        if execution.get("history_complete") and related:
            if all(a["status"] == "matched" for a in related):
                key = "planned"
            elif any(a["status"] == "unreviewed" for a in related) and all(a["status"] != "historical" for a in related):
                key = "unreviewed"
        cohort = cohorts[key]
        cohort["closed_cycles"] += 1
        fees = execution.get("fees") or {}
        cohort["net_usdt"] = str(D(cohort["net_usdt"] or "0") + D(execution.get("realized_pnl", "0")) - D(fees.get("USDT", "0")))
        for asset, amount in fees.items():
            if asset != "USDT":
                cohort["non_usdt_fees"][asset] = str(D(cohort["non_usdt_fees"].get(asset, "0")) + D(amount))
    return {"scope": "since_policy_enabled", "new_risk_orders": len(active), "matched_orders": matched,
            "unreviewed_orders": unreviewed, "pending_match_orders": pending,
            "historical_orders": sum(a["status"] == "historical" for a in audits),
            "pretrade_coverage": _ratio(matched, len(active)),
            # Matching entry terms is observable; full lifecycle adherence is not yet provable.
            "plan_adherence": {"numerator": None, "denominator": None, "ratio": None},
            "entry_parameter_adherence": dict(_ratio(sum(a["status"] == "matched" for a in comparable), len(comparable)),
                                              label="事前计划入场参数匹配率", excluded_pending=excluded),
            "review_completion": _ratio(completed, due),  # Legacy evidence coverage; not personal completion.
            "personal_review_completion": reviews["personal"],
            "ai_report_coverage": reviews["ai"], "review_progress": reviews, "performance": cohorts,
            "limitations": ["计划遵守率尚不能覆盖成交后的止损/风险变更，因此保留为无数据；预审覆盖率只核对新增风险订单。",
                            "对比是观察性结果，不能说明预审造成收益变化；请同时查看每组样本数。",
                            "周期净损益扣除已记录 USDT 手续费，未向周期分摊资金费；非 USDT 费用单列，未换算。",
                            "本人复盘与 AI 报告覆盖近30天已确认平仓的周期，不依赖风险政策；只有平仓后留下的记录才计入。外部截图缺少确认平仓时间时不计入。"]}


def today():
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_ms()
        _refresh(db, stamp)
        policy = _policy(db)
        audits = reconcile_audit(db, stamp)
        date, start, end = day_bounds(stamp)
        dt = datetime.fromtimestamp(stamp / 1000, HK)
        week = (dt.date() - timedelta(days=dt.weekday())).isoformat()
        tasks = [json.loads(row[0]) for row in db.execute("SELECT data FROM training_tasks WHERE week=?", (week,))]
        activities = [json.loads(row[0]) for row in db.execute("SELECT data FROM training_activities WHERE day=? ORDER BY rowid DESC", (date,))]
        intents = [json.loads(row[0]) for row in db.execute("SELECT data FROM training_intents ORDER BY updated DESC LIMIT 50")]
        training_complete = any(a["kind"] in ("observe", "complete_task") for a in activities) or any(
            i["status"] in ("permitted", "matched") and start <= i.get("permit", {}).get("issued_at", 0) < end for i in intents)
        account = journal.kv("training_last_account", {}, db)
        daily = account.get("daily_realized_pnl_usdt") if account.get("day_start") == start else None
        remaining = str(max(D(0), D(policy["max_daily_loss_usdt"]) - max(D(0), -D(daily)))) if policy["configured"] and daily is not None else None
        if not policy["configured"]:
            next_step = "先确认自己的风险政策，再审核第一笔计划。"
        elif any(i["status"] == "cooldown" for i in intents):
            next_step = "当前处于冷静期，可记录观望理由或完成复盘。"
        elif any(i["status"] == "awaiting_confirmation" for i in intents):
            next_step = "审核已完成；核对最新证据后，由本人决定是否确认。"
        elif training_complete:
            next_step = "今天已完成训练，无需为了打卡增加交易。"
        else:
            next_step = "审核一笔计划，或写下一句观望理由完成今日训练。"
        return {"date": date, "timezone": "Asia/Hong_Kong", "generated_at": stamp, "policy": policy, "intents": intents,
                "tasks": tasks, "activities": activities, "training_complete": training_complete, "next_step": next_step,
                "metrics": _metrics(db, audits, policy), "audits": audits[:100],
                "risk_budget": {"max_daily_loss_usdt": policy["max_daily_loss_usdt"], "daily_realized_pnl_usdt": daily,
                                "remaining_usdt": remaining, "source": "最近审核时的完整当日账户证据" if daily is not None else "等待完整账户证据",
                                "as_of": account.get("captured_at") if daily is not None else None}}


def save_activity(payload):
    kind = payload.get("kind")
    if kind not in ("observe", "weekly_task", "complete_task"):
        raise ValueError("训练活动类型不正确。")
    stamp = now_ms()
    date = day_bounds(stamp)[0]
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        activity = {"id": uuid.uuid4().hex, "kind": kind, "reason": "", "title": "", "task_id": None, "created": stamp, "date": date}
        if kind == "observe":
            reason = payload.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                raise ValueError("请写一句观望理由（最多 2000 字）。")
            activity["reason"] = reason.strip()
            previous = db.execute("SELECT id FROM training_activities WHERE day=? AND kind='observe'", (date,)).fetchone()
            if previous:
                activity["id"] = previous[0]
        elif kind == "weekly_task":
            title = payload.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 300:
                raise ValueError("每周任务需要 1–300 字标题。")
            dt = datetime.fromtimestamp(stamp / 1000, HK)
            week = (dt.date() - timedelta(days=dt.weekday())).isoformat()
            if db.execute("SELECT 1 FROM training_tasks WHERE week=?", (week,)).fetchone():
                raise Conflict("本周已经有一个改进任务，先完成当前任务。")
            task = {"id": uuid.uuid4().hex, "title": title.strip(), "status": "pending", "week": week, "created": stamp, "completed_at": None}
            db.execute("INSERT INTO training_tasks VALUES(?,?,?)", (task["id"], week, encoded(task)))
            activity.update(task_id=task["id"], title=task["title"])
        else:
            row = db.execute("SELECT data FROM training_tasks WHERE id=?", (payload.get("task_id"),)).fetchone()
            if not row:
                raise ValueError("找不到这个训练任务。")
            task = json.loads(row[0])
            if task["status"] != "complete":
                task.update(status="complete", completed_at=stamp)
                db.execute("UPDATE training_tasks SET data=? WHERE id=?", (encoded(task), task["id"]))
            activity.update(task_id=task["id"], title=task["title"])
            previous = next((json.loads(r[0]) for r in db.execute("SELECT data FROM training_activities WHERE kind='complete_task'")
                             if json.loads(r[0]).get("task_id") == task["id"]), None)
            if previous:
                activity = previous
        db.execute("INSERT OR REPLACE INTO training_activities VALUES(?,?,?,?)", (activity["id"], activity["date"], activity["kind"], encoded(activity)))
    return today()
