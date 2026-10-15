"""Read-only exchange orders and deterministic planning figures. No trading actions."""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import journal
from binance_client import BinanceError


def dec(value):
    return Decimal(str(value or "0"))


def normalize_orders(rows):
    if not isinstance(rows, list):
        raise BinanceError("挂单接口返回格式不正确。")
    return [{"order_ref": ("algo:" + str(r["algoId"])) if r.get("algoId") is not None else ("order:" + str(r["orderId"])) if r.get("orderId") is not None else None,
             "symbol": r["symbol"], "type": r.get("orderType", r.get("type")), "side": r.get("side"),
             "positionSide": r.get("positionSide"), "triggerPrice": r.get("triggerPrice", r.get("stopPrice", "0")),
             "price": r.get("price", "0"), "quantity": str(max(Decimal(0), dec(r.get("quantity", r.get("origQty", "0"))) - dec(r.get("executedQty")))) if dec(r.get("executedQty")) else r.get("quantity", r.get("origQty", "0")),
             "status": r.get("algoStatus", r.get("status")), "closePosition": r.get("closePosition") is True,
             "reduceOnly": r.get("reduceOnly") is True, "workingType": r.get("workingType"),
             "priceProtect": r.get("priceProtect") is True, "created": r.get("createTime", r.get("time"))}
            for r in rows]


def planning(position, orders, fee_rate, tick="0.01"):
    entry, qty, mark = dec(position["entryPrice"]), abs(dec(position["positionAmt"])), dec(position["markPrice"])
    if not qty or not entry:
        return {}
    long = dec(position["positionAmt"]) > 0
    sign = Decimal(1 if long else -1)
    correct_side = "SELL" if long else "BUY"
    matched = [o for o in orders if o["side"] == correct_side and o["positionSide"] == position["positionSide"]
               and o["status"] in {"NEW", "PARTIALLY_FILLED"} and
               (o["closePosition"] or dec(o["quantity"]) >= qty)]
    stops = [o for o in matched if o["type"] in {"STOP", "STOP_MARKET"} and dec(o["triggerPrice"]) > 0]
    targets = [o for o in matched if o["type"] in {"TAKE_PROFIT", "TAKE_PROFIT_MARKET"} and dec(o["triggerPrice"]) > 0]
    result = {"entry": str(entry), "quantity": str(qty), "mark_price": str(mark),
              "price_change_pct": str((mark / entry - 1) * sign * 100),
              "entry_notional": str(entry * qty), "tick_size": tick,
              "price_up_1pct": str(entry * Decimal("1.01")), "price_up_2pct": str(entry * Decimal("1.02"))}
    # Binance breakEvenPrice incorporates the exchange's posted cost basis; closing fee is additional.
    basis = dec(position.get("breakEvenPrice"))
    if basis and fee_rate is not None:
        fee = dec(fee_rate)
        net = basis / (1 - fee if long else 1 + fee)
        step = dec(tick)
        rounded = (net / step).to_integral_value(rounding=ROUND_CEILING if long else ROUND_FLOOR) * step
        result.update(exchange_break_even=str(basis), estimated_net_break_even=str(rounded), closing_fee_rate=str(fee))
    if len(stops) == 1:
        stop = dec(stops[0]["triggerPrice"])
        result.update(stop_loss=str(stop), gross_stop_loss=str((entry - stop) * sign * qty),
                      additional_loss_to_stop=str(max(Decimal(0), (mark - stop) * sign * qty)))
    if len(targets) == 1:
        target = dec(targets[0]["triggerPrice"])
        result.update(take_profit=str(target), gross_target_profit=str((target - entry) * sign * qty))
    if dec(result.get("gross_stop_loss")) > 0 and dec(result.get("gross_target_profit")) > 0:
        result["rr"] = str(dec(result["gross_target_profit"]) / dec(result["gross_stop_loss"]))
    return result


def collect(client, symbols, position_rows=None):
    results = {}
    for symbol in sorted(set(symbols)):
        try:
            positions = position_rows if position_rows is not None else client.get("/fapi/v3/positionRisk", {"symbol": symbol}, True)
            positions = [p for p in positions if p["symbol"] == symbol]
            positions = [p for p in positions if dec(p["positionAmt"])]
            regular = client.get("/fapi/v1/openOrders", {"symbol": symbol}, True)
            algo = client.get("/fapi/v1/openAlgoOrders", {"symbol": symbol}, True)
            orders = normalize_orders(regular) + normalize_orders(algo)
            cache = getattr(client, "context_cache", {})
            client.context_cache = cache
            if journal.now_ms() - cache.get("at", 0) > 1800000:
                cache.clear()
                cache.update(at=journal.now_ms(), exchange=client.get("/fapi/v1/exchangeInfo"))
            if symbol not in cache:
                cache[symbol] = client.get("/fapi/v1/commissionRate", {"symbol": symbol}, True)
            fees = cache[symbol]
            exchange = cache["exchange"]
            info = next(s for s in exchange["symbols"] if s["symbol"] == symbol)
            tick = next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER")
            results[symbol] = {"status": "complete", "captured_at": journal.now_ms(), "orders": orders,
                               "position_count": len(positions), "positions": positions,
                               "planning": planning(positions[0], orders, fees["takerCommissionRate"], tick) if len(positions) == 1 else {},
                               "note": "挂单快照，不保证触发后的成交价格；费用保本价未包含未来资金费和滑点。"}
        except (BinanceError, KeyError, StopIteration, TypeError) as e:
            results[symbol] = {"status": "failed", "attempted": journal.now_ms(),
                               "error": str(e) if isinstance(e, BinanceError) else "挂单上下文不完整，请重新同步。"}
    return results
