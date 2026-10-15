"""A deliberately read-only client. Credentials never reach the frontend or AI."""
import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://fapi.binance.com"
READ_PATHS = {"/fapi/v1/time", "/fapi/v3/positionRisk", "/fapi/v1/userTrades",
              "/fapi/v1/income", "/fapi/v1/klines", "/fapi/v1/openOrders",
              "/fapi/v1/openAlgoOrders", "/fapi/v1/commissionRate",
              "/fapi/v1/exchangeInfo", "/fapi/v1/premiumIndex", "/fapi/v3/account",
              "/futures/data/openInterestHist"}


class BinanceError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BinanceError("Binance 返回重定向，已停止请求。")


def credentials(root):
    values = {}
    path = Path(root) / ".env.local"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values.get("BINANCE_API_KEY", ""), values.get("BINANCE_API_SECRET", "")


class Binance:
    def __init__(self, key="", secret=""):
        self.key, self.secret, self.offset = key, secret, 0
        context = ssl.create_default_context()
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def get(self, path, params=None, signed=False):
        if path not in READ_PATHS:
            raise BinanceError("此应用只允许读取持仓、成交、账单和行情。")
        params = dict(params or {})
        headers = {"User-Agent": "LocalTradeJournal/1.0"}
        if signed:
            if not self.key or not self.secret:
                raise BinanceError("尚未配置 Binance 只读 API 密钥。")
            params.update(timestamp=int(time.time() * 1000) + self.offset, recvWindow=10000)
            headers["X-MBX-APIKEY"] = self.key
        query = urllib.parse.urlencode(params)
        if signed:
            query += "&signature=" + hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(BASE + path + "?" + query, headers=headers, method="GET")
        try:
            with self.opener.open(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
                code = body.get("code", e.code)
            except (ValueError, AttributeError):
                code = e.code
            messages = {-2015: "密钥、IP 白名单或合约读取权限不匹配。请在 Binance 检查后重试。",
                        -2014: "API 密钥格式无效。", -1022: "签名校验失败，请检查 API Secret。",
                        -1021: "本机与 Binance 时间不一致，请重试同步。",
                        451: "Binance 当前网络地区不可用。", 429: "Binance 请求频率受限，请稍后重试。",
                        418: "Binance 暂时限制了当前 IP，请等待限制解除。"}
            raise BinanceError(f"Binance {code}：{messages.get(code, '读取失败，请检查网络与账户 API 设置。')}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BinanceError("无法连接 Binance，请检查本机网络。") from None

    def align_time(self):
        before = int(time.time() * 1000)
        now = self.get("/fapi/v1/time")["serverTime"]
        self.offset = now - (before + int(time.time() * 1000)) // 2
        return now

    def trade_window(self, symbol, start, end):
        rows = self.get("/fapi/v1/userTrades", {"symbol": symbol, "startTime": start, "endTime": end, "limit": 1000}, True)
        if len(rows) < 1000:
            return rows
        # Split instead of advancing the last timestamp: multiple fills may share a millisecond.
        if start >= end:
            raise BinanceError("单毫秒成交超过接口上限，本次同步未提交；请导入交易所导出记录。")
        middle = (start + end) // 2
        return self.trade_window(symbol, start, middle) + self.trade_window(symbol, middle + 1, end)

    def trades(self, symbol, start, end):
        rows = []
        cursor = start
        while cursor <= end:
            stop = min(cursor + 7 * 86400000 - 1, end)
            rows.extend(self.trade_window(symbol, cursor, stop))
            cursor = stop + 1
        return rows

    def income(self, start, end):
        rows = []
        for page in range(1, 101):
            batch = self.get("/fapi/v1/income", {"startTime": start, "endTime": end, "limit": 1000, "page": page}, True)
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
        raise BinanceError("账单超过本次读取上限；本次同步未提交，请缩短同步范围。")
