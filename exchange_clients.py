"""Read-only OKX V5 and Bybit V5 adapters for USDT-settled perpetuals.

Only allowlisted GETs, fixed official hosts and verified TLS. Raw remote errors
are never surfaced: they can contain credentials or request signatures.
"""
import base64
import hashlib
import hmac
import json
import ssl
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from ledger import exact, amount
from urllib import request, parse, error

DAY = 86_400_000
NAMES = {"okx": "OKX", "bybit": "Bybit"}
PATHS = {
    "okx": {"/api/v5/public/time", "/api/v5/public/instruments", "/api/v5/account/config",
            "/api/v5/account/balance", "/api/v5/account/positions", "/api/v5/trade/fills-history",
            "/api/v5/account/bills-archive", "/api/v5/trade/orders-pending", "/api/v5/trade/orders-algo-pending"},
    "bybit": {"/v5/market/time", "/v5/market/instruments-info", "/v5/user/query-api",
              "/v5/account/wallet-balance", "/v5/position/list", "/v5/order/realtime",
              "/v5/order/history", "/v5/execution/list", "/v5/account/transaction-log"}}
PUBLIC = {"/api/v5/public/time", "/api/v5/public/instruments", "/v5/market/time", "/v5/market/instruments-info"}


class ExchangeError(ValueError):
    pass


def decimal(value):
    try:
        result = amount(value)
        if result.is_finite():
            return result
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise ExchangeError("交易所数值字段缺失或无效，本轮未提交，等待重新核对。")


def positive(value):
    result = decimal(value)
    if result <= 0:
        raise ExchangeError("交易所数量或价格无效，本轮未提交。")
    return result


def flag(value):
    return value is True or value == "true"


def windows(start, end):
    while start <= end:
        stop = min(start + 7 * DAY - 1, end)
        yield start, stop
        start = stop + 1


def side(value):
    value = str(value).upper()
    if value not in {"BUY", "SELL"}:
        raise ExchangeError("成交方向缺失，等待重新核对。")
    return value


def leg(value):
    try:
        return {0: "BOTH", 1: "LONG", 2: "SHORT"}[int(value)]
    except (KeyError, TypeError, ValueError):
        raise ExchangeError("Bybit 持仓模式缺失，无法区分单向与双向持仓。") from None


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ExchangeError("交易所返回重定向，已停止请求。请核对账户所在地区。")


class Client:
    def __init__(self, exchange, credentials):
        if exchange not in NAMES:
            raise ExchangeError("交易所不支持。")
        self.exchange, self.credentials, self.offset = exchange, credentials, 0
        self.base = {"okx": "https://www.okx.com", "bybit": "https://api.bybit.com"}[exchange]
        context = ssl.create_default_context()
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            # python.org builds on macOS can have an empty OpenSSL trust store.
            # Load the OS CA bundle; never disable verification or trust remote certificates.
            if not context.cert_store_stats()["x509_ca"]:
                for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
                    if Path(bundle).is_file():
                        context.load_verify_locations(cafile=bundle)
                        break
        self.opener = request.build_opener(NoRedirect(), request.HTTPSHandler(context=context))
        self.instruments = {}

    def headers(self, path, query, stamp):
        headers = {"User-Agent": "TraderDashboard/1.0", "Accept": "application/json"}
        if path in PUBLIC:
            return headers
        key, secret = self.credentials["key"], self.credentials["secret"]
        if self.exchange == "okx":
            iso = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            message = iso + "GET" + path + ("?" + query if query else "")
            signature = base64.b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).decode()
            headers.update({"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": signature, "OK-ACCESS-TIMESTAMP": iso,
                            "OK-ACCESS-PASSPHRASE": self.credentials["passphrase"]})
        else:
            message = str(stamp) + key + "5000" + query
            headers.update({"X-BAPI-API-KEY": key, "X-BAPI-SIGN": hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest(),
                            "X-BAPI-TIMESTAMP": str(stamp), "X-BAPI-RECV-WINDOW": "5000"})
        return headers

    def get(self, path, params=None):
        if path not in PATHS[self.exchange]:
            raise ExchangeError("此连接仅支持读取账户、持仓、成交与挂单。")
        query = parse.urlencode(params or {})
        req = request.Request(self.base + path + ("?" + query if query else ""), method="GET",
                              headers=self.headers(path, query, int(time.time() * 1000) + self.offset))
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(12_000_001)
            if len(raw) > 12_000_000:
                raise ExchangeError("交易所响应过大，本轮未提交。")
            data = json.loads(raw)
        except error.HTTPError as exc:
            message = {429: "请求频率受限，请稍后重试。", 403: "IP、地区或 API 权限不匹配。",
                       401: "API 凭证校验失败。", 451: "当前服务器地区不可用。"}.get(exc.code, "请求失败，稍后自动重试。")
            raise ExchangeError(NAMES[self.exchange] + "：" + message) from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            raise ExchangeError(NAMES[self.exchange] + "：网络、TLS 或响应异常，已有快照保留。") from None
        code = str(data.get("code" if self.exchange == "okx" else "retCode", "invalid"))
        if code != "0":
            message = {"50113": "签名校验失败，请检查 Secret。", "50111": "API Key 无效。",
                       "50105": "Passphrase 不匹配。", "50102": "时间校验失败。", "50011": "请求频率受限。",
                       "10001": "账户类型或参数不支持。", "10002": "时间校验失败。", "10003": "API Key 或地区不匹配。",
                       "10004": "签名校验失败。", "10005": "API 读取权限不足。", "10006": "请求频率受限。",
                       "10010": "服务器 IP 不在 API 白名单。", "10028": "需要统一交易账户。"}.get(code, "读取失败，请核对 API 权限、账户类型和地区。")
            raise ExchangeError(NAMES[self.exchange] + "：" + message)
        result = data.get("data" if self.exchange == "okx" else "result")
        if not isinstance(result, list if self.exchange == "okx" else dict):
            raise ExchangeError("交易所响应格式不正确。")
        return result

    def align(self):
        before = int(time.time() * 1000)
        data = self.get("/api/v5/public/time" if self.exchange == "okx" else "/v5/market/time")
        remote = int(data[0]["ts"]) if self.exchange == "okx" else int(data["timeSecond"]) * 1000
        self.offset = remote - (before + int(time.time() * 1000)) // 2

    def identity(self):
        if self.exchange == "okx":
            data = self.get("/api/v5/account/config")[0]
            uid = str(data.get("uid", ""))
        else:
            data = self.get("/v5/user/query-api")
            if int(data.get("uta", 0)) != 1:
                raise ExchangeError("当前仅支持 Bybit 统一交易账户（UTA），不支持经典账户。")
            uid = str(data.get("userID", ""))
        if not uid or uid == "None":
            raise ExchangeError("无法核对账户身份，本轮未提交。")
        # Never persist the identity response: Bybit includes apiKey in it.
        return hashlib.sha256((self.exchange + ":" + uid).encode()).hexdigest()

    def pages(self, path, params, id_field="billId", limit=100):
        params = dict(params, limit=limit)
        rows, seen = [], set()
        for _ in range(200):
            result = self.get(path, params)
            batch = result if self.exchange == "okx" else result.get("list")
            if not isinstance(batch, list):
                raise ExchangeError("交易所分页响应不完整。")
            rows.extend(batch)
            cursor = str(batch[-1].get(id_field, "")) if self.exchange == "okx" and len(batch) == limit else ""
            if self.exchange == "bybit":
                cursor = result.get("nextPageCursor") or ""
            if not cursor:
                if self.exchange == "okx" and len(batch) == limit:
                    raise ExchangeError("交易所分页游标缺失，本轮未提交。")
                return rows
            if cursor in seen or not batch:
                raise ExchangeError("交易所分页未前进，本轮未提交。")
            seen.add(cursor)
            params["after" if self.exchange == "okx" else "cursor"] = cursor
            time.sleep(0.12)
        raise ExchangeError("交易历史超过本次分页上限，请缩短读取范围。")

    def load_instruments(self, cached=None):
        self.instruments = dict(cached or {})  # Keep delisted metadata already verified in prior syncs.
        if self.exchange == "okx":
            rows = self.get("/api/v5/public/instruments", {"instType": "SWAP"})
            self.instruments.update({r["instId"]: r for r in rows if r.get("settleCcy") == "USDT" and r.get("ctType") == "linear"})
        else:
            rows = self.pages("/v5/market/instruments-info", {"category": "linear"}, limit=1000)
            self.instruments.update({r["symbol"]: r for r in rows if r.get("settleCoin") == "USDT" and r.get("contractType") == "LinearPerpetual"})

    def supported(self, instrument):
        # Dated futures and USDC/inverse contracts are explicitly out of scope.
        candidate = instrument.endswith("-USDT-SWAP") if self.exchange == "okx" else instrument.endswith("USDT") and "-" not in instrument
        if not candidate:
            return False
        if instrument not in self.instruments:
            raise ExchangeError("缺少 USDT 永续合约元数据，可能为已下架合约；保留已有记录，请核对交易所导出账单。")
        return True

    def quantity(self, instrument, contracts):
        value = decimal(contracts)
        if self.exchange == "okx":
            spec = self.instruments[instrument]
            if spec.get("ctValCcy") != instrument.removesuffix("-USDT-SWAP"):
                raise ExchangeError("OKX 合约面值币种不支持，不能可靠换算基础币数量。")
            value *= positive(spec.get("ctVal")) * positive(spec.get("ctMult"))
        return str(value)

    def symbol(self, instrument):
        return instrument.replace("-USDT-SWAP", "USDT") if self.exchange == "okx" else instrument

    def positions(self, rows):
        result = []
        for row in rows:
            instrument = row["instId" if self.exchange == "okx" else "symbol"]
            raw_qty = decimal(row["pos" if self.exchange == "okx" else "size"])
            if not raw_qty or not self.supported(instrument):
                continue
            if self.exchange == "okx":
                ps = {"long": "LONG", "short": "SHORT", "net": "BOTH"}.get(row.get("posSide"))
                if not ps:
                    raise ExchangeError("OKX 仓位方向缺失。")
                qty = decimal(self.quantity(instrument, raw_qty))
                if ps == "SHORT": qty = -abs(qty)
                entry, mark, pnl, liq, updated = (row.get(k) for k in ("avgPx", "markPx", "upl", "liqPx", "uTime"))
            else:
                ps = leg(row.get("positionIdx"))
                qty = raw_qty * (1 if side(row.get("side")) == "BUY" else -1)
                entry, mark, pnl, liq, updated = (row.get(k) for k in ("avgPrice", "markPrice", "unrealisedPnl", "liqPrice", "updatedTime"))
            result.append(dict(symbol=self.symbol(instrument), positionSide=ps, positionAmt=str(qty),
                               entryPrice=str(positive(entry)), markPrice=str(positive(mark)), unRealizedProfit=str(decimal(pnl)),
                               liquidationPrice=liq or "", updateTime=int(updated), notional=str(abs(qty) * positive(mark))))
        return result

    def fills(self, rows, bills=None, orders=None):
        result = []
        bill_map = {}
        for bill in bills or []:
            if bill.get("type") in {"TRADE", "LIQUIDATION", "ADL", "DELIVERY"}:
                bill_map.setdefault((bill.get("symbol"), bill.get("tradeId")), []).append(bill)
        # Repeated pages may include the same bill; do not double-count cashFlow.
        bill_map = {k: list({b["id"]: b for b in v}.values()) for k, v in bill_map.items()}
        order_map = {o["orderId"]: o for o in orders or []}
        for row in rows:
            instrument = row["instId" if self.exchange == "okx" else "symbol"]
            if not self.supported(instrument): continue
            if self.exchange == "okx":
                ps = {"long": "LONG", "short": "SHORT", "net": "BOTH"}.get(row.get("posSide"))
                if not ps: raise ExchangeError("OKX 成交持仓方向缺失。")
                fid, oid, stamp = row["tradeId"], row["ordId"], row["fillTime"]
                qty, price, pnl = self.quantity(instrument, row["fillSz"]), row["fillPx"], row["fillPnl"]
                fee, asset = -decimal(row["fee"]), row["feeCcy"]
            else:
                if row.get("execType") not in {"Trade", "AdlTrade", "BustTrade"}: continue
                fid, oid, stamp = row["execId"], row["orderId"], row["execTime"]
                matching = bill_map.get((instrument, fid), [])
                order = order_map.get(oid)
                if not matching or not order:
                    raise ExchangeError("Bybit 成交、账单或订单方向尚未对齐，等待自动补读；本轮未提交。")
                ps = leg(order.get("positionIdx"))
                qty, price = row["execQty"], row["execPrice"]
                pnl = sum((decimal(b["cashFlow"]) for b in matching), Decimal(0))
                currencies = {b["currency"] for b in matching}
                asset = row.get("feeCurrency") or (next(iter(currencies)) if len(currencies) == 1 else "")
                fee = decimal(row["execFee"])
            if not asset: raise ExchangeError("成交手续费币种缺失，等待核对。")
            result.append(dict(id=str(fid), orderId=str(oid), time=int(stamp), symbol=self.symbol(instrument), positionSide=ps,
                               side=side(row["side"]), qty=str(positive(qty)), price=str(positive(price)), realizedPnl=str(decimal(pnl)),
                               commission=str(fee), commissionAsset=asset, exchange=self.exchange, sequence=str(row.get("seq", fid if str(fid).isdigit() else "0"))))
        return result

    @exact
    def income(self, rows):
        result, seen = [], {}
        for row in rows:
            instrument = row.get("instId" if self.exchange == "okx" else "symbol", "")
            if instrument and not self.supported(instrument):
                continue
            if self.exchange == "okx":
                asset, stamp, ident = row["ccy"], row["ts"], row["billId"]
                kind = str(row.get("type", ""))
                pnl, fee = decimal(row.get("pnl") or "0"), decimal(row.get("fee") or "0")
                change = decimal(row["balChg"]) if row.get("balChg") not in (None, "") else pnl + fee
                if kind in {"2", "3", "5", "9"} and instrument:
                    # Trades, delivery/delisting, liquidation and ADL: keep all components.
                    if row.get("pnl") in (None, "") or row.get("fee") in (None, ""):
                        raise ExchangeError("OKX 盈亏或费用字段缺失，本轮未提交。")
                    values = {"REALIZED_PNL": pnl, "COMMISSION": fee}
                elif kind == "8" and str(row.get("subType")) in {"173", "174"} and instrument:
                    if row.get("pnl") in (None, ""):
                        raise ExchangeError("OKX 资金费缺失，本轮未提交。")
                    values = {"FUNDING_FEE": pnl, "COMMISSION": fee}
                elif kind == "1":
                    values = {"TRANSFER": change}
                else:
                    values = {"UNCLASSIFIED_OKX_" + kind: change}
                # Cross-margin bills reconcile directly; isolated margin movement is not P/L.
                if kind in {"2", "3", "5", "8", "9"} and row.get("mgnMode") == "cross" and row.get("balChg") not in (None, "") and sum(values.values()) != change:
                    raise ExchangeError("OKX 账单余额变动与盈亏费用不一致，本轮未提交。")
            else:
                asset, stamp, ident = row["currency"], row["transactionTime"], row["id"]
                kind = row.get("type", "")
                cash, fee = decimal(row["cashFlow"]), decimal(row["fee"])
                funding = decimal(row.get("funding") or "0")
                change = decimal(row["change"])
                if cash + funding - fee != change:
                    raise ExchangeError("Bybit 账单变动不等于现金流加资金费减手续费，本轮未提交。")
                if kind in {"TRADE", "SETTLEMENT", "LIQUIDATION", "ADL", "DELIVERY"} and instrument and not row.get("extraFees"):
                    values = {"REALIZED_PNL": cash, "COMMISSION": -fee, "FUNDING_FEE": funding}
                elif kind == "FEE_REFUND" and not funding and not row.get("extraFees"):
                    values = {"COMMISSION_REBATE": change}
                elif kind in {"TRANSFER_IN", "TRANSFER_OUT"}:
                    values = {"TRANSFER": change}
                elif kind in {"BONUS", "BONUS_RECOLLECT", "BONUS_TRANSFER_IN", "BONUS_TRANSFER_OUT"}:
                    values = {"BONUS": change}
                else:
                    values = {"UNCLASSIFIED_BYBIT_" + str(kind): change}
            if not isinstance(asset, str) or not asset or type(ident) not in (str, int) or not str(ident).strip():
                raise ExchangeError("账单币种或编号缺失，本轮未提交。")
            if asset != "USDT" and any(k in {"REALIZED_PNL", "FUNDING_FEE"} for k in values):
                raise ExchangeError("USDT 永续账单存在其他结算币种，暂不合并收益，请核对账单。")
            for category, value in values.items():
                normalized = dict(tranId=str(ident), incomeType=category, income=str(amount(value)), asset=asset,
                                  time=int(stamp), symbol=self.symbol(instrument), exchange=self.exchange,
                                  source_type=kind, unclassified=category.startswith("UNCLASSIFIED_"))
                key = (category, str(ident))
                if key in seen:
                    if seen[key] != normalized:
                        raise ExchangeError("同一账单编号存在冲突，本轮未提交。")
                    continue
                seen[key] = normalized
                result.append(normalized)
        return result

    def orders(self, rows):
        result = []
        for row in rows:
            instrument = row.get("instId" if self.exchange == "okx" else "symbol", "")
            if not self.supported(instrument): continue
            if self.exchange == "okx":
                ps = {"long": "LONG", "short": "SHORT", "net": "BOTH"}.get(row.get("posSide"))
                if not ps: raise ExchangeError("OKX 挂单持仓方向缺失。")
                algo = bool(row.get("algoId"))
                stop = decimal(row.get("slTriggerPx") or "0") > 0
                take = decimal(row.get("tpTriggerPx") or "0") > 0
                kind = "STOP" if stop else "TAKE_PROFIT" if take else "TRAILING_STOP_MARKET" if row.get("ordType") == "move_order_stop" else row.get("ordType", "UNKNOWN").upper()
                trigger = row.get("slTriggerPx") if stop else row.get("tpTriggerPx") if take else row.get("triggerPx", "0")
                market = (row.get("slOrdPx") if stop else row.get("tpOrdPx") if take else row.get("ordPx")) == "-1"
                if kind in {"STOP", "TAKE_PROFIT"} and market: kind += "_MARKET"
                close = decimal(row.get("closeFraction") or "0") == 1
                quantity = self.quantity(instrument, max(Decimal(0), decimal(row.get("sz") or "0") - decimal(row.get("accFillSz") or "0")))
                oid = ("algo:" + row["algoId"]) if algo else row["ordId"]
                working = row.get("slTriggerPxType" if stop else "tpTriggerPxType", "last")
                reduce = flag(row.get("reduceOnly"))
                status = "NEW" if row.get("state") == "live" else "PARTIALLY_FILLED" if row.get("state") == "partially_filled" else "UNKNOWN"
            else:
                ps, oid = leg(row.get("positionIdx")), row["orderId"]
                trigger = row.get("triggerPrice") or "0"
                name = row.get("stopOrderType", "")
                stop = name in {"StopLoss", "PartialStopLoss"}
                # Generic conditional close stops must trigger against the position direction.
                if name == "Stop" and (flag(row.get("reduceOnly")) or flag(row.get("closeOnTrigger"))):
                    stop = (side(row["side"]), int(row.get("triggerDirection", 0))) in {("SELL", 2), ("BUY", 1)}
                kind = "STOP" if stop else "TAKE_PROFIT" if name in {"TakeProfit", "PartialTakeProfit"} else "CONDITIONAL" if decimal(trigger) else row["orderType"].upper()
                if kind in {"STOP", "TAKE_PROFIT"} and row.get("orderType") == "Market": kind += "_MARKET"
                # qty=0 + reduceOnly + closeOnTrigger is Bybit's documented close-all form.
                close = decimal(row.get("qty") or "0") == 0 and flag(row.get("reduceOnly")) and flag(row.get("closeOnTrigger"))
                quantity = str(decimal(row.get("leavesQty") if row.get("leavesQty") not in (None, "") else row.get("qty")))
                reduce, working = flag(row.get("reduceOnly")) or flag(row.get("closeOnTrigger")), row.get("triggerBy")
                status = {"New": "NEW", "Untriggered": "NEW", "PartiallyFilled": "PARTIALLY_FILLED"}.get(row.get("orderStatus"), "UNKNOWN")
            result.append(dict(symbol=self.symbol(instrument), positionSide=ps, order_ref=str(oid), side=side(row["side"]),
                               type=kind, status=status, triggerPrice=trigger or "0", quantity=quantity, closePosition=close,
                               reduceOnly=reduce, workingType=working, priceProtect=False, price=row.get("px", row.get("price", "")),
                               coverage_unknown=kind in {"TRIGGER", "CONDITIONAL"}))
            if self.exchange == "okx" and stop and take and row.get("ordType") == "oco":
                result.append(dict(result[-1], order_ref=str(oid) + ":tp", type="TAKE_PROFIT_MARKET" if row.get("tpOrdPx") == "-1" else "TAKE_PROFIT",
                                   triggerPrice=row["tpTriggerPx"], workingType=row.get("tpTriggerPxType", "last")))
        return result

    def fetch(self, start, end, cached=None):
        self.load_instruments(cached)
        if self.exchange == "okx":
            positions = self.get("/api/v5/account/positions", {"instType": "SWAP"})
            account = self.get("/api/v5/account/balance", {"ccy": "USDT"})
            fills = self.pages("/api/v5/trade/fills-history", {"instType": "SWAP", "begin": start, "end": end})
            bills = self.pages("/api/v5/account/bills-archive", {"instType": "SWAP", "begin": start, "end": end})
            orders = self.pages("/api/v5/trade/orders-pending", {"instType": "SWAP"}, "ordId")
            for kind in ("conditional", "oco", "trigger", "move_order_stop"):
                orders += self.pages("/api/v5/trade/orders-algo-pending", {"instType": "SWAP", "ordType": kind}, "algoId")
            fill_orders = []
            coin = next((c for a in account for c in a.get("details", []) if c.get("ccy") == "USDT"), {})
            wallet = {"equity": coin.get("eq"), "asset": "USDT", "scope": "USDT 币种权益（含其他 USDT 产品）"}
        else:
            positions = self.pages("/v5/position/list", {"category": "linear", "settleCoin": "USDT"}, limit=200)
            account = self.get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
            orders = self.pages("/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "openOnly": 0}, limit=50)
            fills, bills, fill_orders = [], [], list(orders)
            for left, right in windows(start, end):
                query = {"category": "linear", "startTime": left, "endTime": right}
                fills += self.pages("/v5/execution/list", query, limit=100)
                # Read all fee currencies; normalization rejects unconvertible settlement totals.
                bills += self.pages("/v5/account/transaction-log", dict(query, accountType="UNIFIED"), limit=50)
                fill_orders += self.pages("/v5/order/history", query, limit=50)
            # Order history is indexed by creation time. An old resting order may fill in this window.
            missing = {f["orderId"] for f in fills if f.get("execType") in {"Trade", "AdlTrade", "BustTrade"} and self.supported(f["symbol"])} - {o["orderId"] for o in fill_orders}
            if len(missing) > 100:
                raise ExchangeError("Bybit 待核对的历史订单较多，请先选择近 7 天同步。")
            for oid in missing:
                fill_orders += self.pages("/v5/order/history", {"category": "linear", "orderId": oid}, limit=50)
            coin = next((c for a in account.get("list", []) for c in a.get("coin", []) if c.get("coin") == "USDT"), {})
            wallet = {"equity": coin.get("equity"), "asset": "USDT", "scope": "统一账户 USDT 币种权益（含其他产品）"}
        # Do not persist account-wide wallet blobs, API identity responses or auth headers.
        return {"positions": self.positions(positions), "orders": self.orders(orders),
                "income": self.income(bills), "trades": self.fills(fills, bills, fill_orders),
                "wallet": wallet, "instruments": self.instruments,
                "raw": {"positions": positions, "orders": orders, "fills": fills, "bills": bills}}
