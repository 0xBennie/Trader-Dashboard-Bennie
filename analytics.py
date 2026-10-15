"""Ledger-backed review views. No reconstructed trade win rate or estimated account equity."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

HK = ZoneInfo("Asia/Hong_Kong")
KINDS = {"REALIZED_PNL": "realized", "COMMISSION": "fees", "FUNDING_FEE": "funding"}


def date_key(stamp):
    return datetime.fromtimestamp(stamp / 1000, HK).date().isoformat()


def bucket():
    return dict(realized=Decimal(0), fees=Decimal(0), funding=Decimal(0), net=Decimal(0),
                fills=0, orders=set(), entries=[])


def build_analytics(income, trades, coverage):
    if not coverage.get("complete"):
        return {"available": False}
    start, end = int(coverage["start"]), int(coverage["end"])
    first, last = datetime.fromtimestamp(start / 1000, HK).date(), datetime.fromtimestamp(end / 1000, HK).date()
    daily = {(first + timedelta(days=i)).isoformat(): bucket() for i in range((last-first).days+1)}
    symbols, running, peak, drawdown = {}, Decimal(0), Decimal(0), Decimal(0)
    for row in sorted(income, key=lambda r: (r["time"], str(r.get("tranId", "")))):
        kind = KINDS.get(row["incomeType"])
        if row["asset"] != "USDT" or not kind or not start <= row["time"] <= end:
            continue
        symbol = row.get("symbol") or "未归属"
        value = Decimal(row["income"])
        for group in (daily[date_key(row["time"])], symbols.setdefault(symbol, bucket())):
            group[kind] += value
            group["net"] += value
            group["entries"].append({"time": row["time"], "symbol": symbol, "kind": kind, "amount": str(value)})
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    for trade in trades:
        if not trade["symbol"].endswith("USDT") or not start <= trade["time"] <= end:
            continue
        for group in (daily[date_key(trade["time"])], symbols.setdefault(trade["symbol"], bucket())):
            group["fills"] += 1
            group["orders"].add((trade["symbol"], str(trade.get("orderId", "fill:"+str(trade["id"])))))

    def export(key, group):
        return {"key": key, **{k: str(group[k]) for k in ("realized", "fees", "funding", "net")},
                "fill_count": group["fills"], "order_count": len(group["orders"]),
                "ledger_count": len(group["entries"]), "entries": group["entries"],
                "active": bool(group["fills"] or group["entries"])}

    rows = [export(key, value) for key, value in daily.items()]
    cumulative = Decimal(0)
    for row in rows:
        cumulative += Decimal(row["net"])
        row["cumulative"] = str(cumulative)
        row["partial"] = (row["key"] == first.isoformat() and datetime.fromtimestamp(start/1000, HK).time().isoformat() != "00:00:00") or row["key"] == last.isoformat()
    active = [row for row in rows if row["active"]]
    return {"available": True, "timezone": "Asia/Hong_Kong", "asset": "USDT", "days": rows,
            "symbols": sorted([export(key, value) for key, value in symbols.items()], key=lambda r: Decimal(r["net"]), reverse=True),
            "summary": {"active_days": len(active), "positive_days": sum(Decimal(r["net"])>0 for r in active),
                        "negative_days": sum(Decimal(r["net"])<0 for r in active),
                        "best": max(active, key=lambda r: Decimal(r["net"]))["key"] if active else None,
                        "worst": min(active, key=lambda r: Decimal(r["net"]))["key"] if active else None,
                        "max_ledger_drawdown": str(drawdown), "net": str(running)}}
