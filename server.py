"""Private trading journal. No execution/trading endpoints exist."""
import argparse
import csv
import fcntl
import io
import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from decimal import Decimal
import ai_review
import ai_provider
import journal
import trade_context
import stop_coverage
import recorder
import training
import pretrade_ai
import pretrade_evidence
import auth
from binance_client import Binance, BinanceError, credentials

ROOT = journal.ROOT
TOKEN = secrets.token_urlsafe(32)
SYNC_LOCK = threading.Lock()
pretrade_evidence.SYNC_LOCK = SYNC_LOCK


def sync_account(days=30):
    if not SYNC_LOCK.acquire(blocking=False):
        raise ValueError("正在同步，请稍后再试。")
    try:
        with journal.connect() as db:
            journal.set_kv(db, "sync", {"status": "running", "started": journal.now_ms()})
        client = Binance(*credentials(ROOT))
        end = client.align_time()
        start = end - int(days) * 86400000
        positions = client.get("/fapi/v3/positionRisk", signed=True)
        positions = [p for p in positions if Decimal(p["positionAmt"]) != 0]
        income = client.income(start, end)
        with journal.connect() as db:
            symbols = {j["symbol"] for j in journal.binance_journals(db)}
        symbols.update(p["symbol"] for p in positions)
        symbols.update(r["symbol"] for r in income if r.get("symbol"))
        symbols = sorted(s for s in symbols if s.endswith("USDT"))
        if len(symbols) > 50:
            raise ValueError("30 天内交易对超过 50 个，请将同步范围缩短至 7 天。")
        trades = []
        for symbol in symbols:
            trades.extend(client.trades(symbol, start, end))
        # Current orders are a separate snapshot; failure here never implies there are no orders.
        with journal.connect() as db:
            context_symbols = {j["symbol"] for j in journal.binance_journals(db)}
        context_symbols.update(p["symbol"] for p in positions)
        contexts = trade_context.collect(client, context_symbols)
        snapshot = {"positions": positions, "captured_at": journal.now_ms(),
                    "coverage": {"start": start, "end": end, "days": days, "symbols": symbols,
                                 "scope": "Binance USDⓈ-M；USDT 成交，全部账单币种分列", "complete": True}}
        with journal.connect() as db:
            for t in trades:
                db.execute("INSERT OR REPLACE INTO trades VALUES(?,?,?,?)", (t["symbol"], str(t["id"]), t["time"], json.dumps(t)))
            for item in income:
                db.execute("INSERT OR REPLACE INTO income VALUES(?,?,?,?)", (item["incomeType"], str(item["tranId"]), item["time"], json.dumps(item)))
            journal.set_kv(db, "snapshot", snapshot)
            journal.set_kv(db, "trade_context", contexts)
            journal.set_kv(db, "sync", {"status": "complete", "completed": journal.now_ms(), "trades": len(trades), "income": len(income)})
        return snapshot
    except (BinanceError, ValueError) as e:
        with journal.connect() as db:
            journal.set_kv(db, "sync", {"status": "failed", "attempted": journal.now_ms(), "error": str(e)})
        return None
    except Exception:
        with journal.connect() as db:
            journal.set_kv(db, "sync", {"status": "failed", "attempted": journal.now_ms(), "error": "同步未完成，已有记录保持不变。"})
        return None
    finally:
        SYNC_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log API payloads, signatures, or credentials.

    def allowed_host(self):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.authentication and self.authentication.public_origin:
            hosts.add(urlparse(self.authentication.public_origin).netloc)
        return self.headers.get("Host", "").lower() in hosts

    @property
    def authentication(self):
        return getattr(self.server, 'auth', None)

    def public_request(self):
        return bool(self.authentication and self.authentication.public_origin and
                    self.headers.get('Host', '').lower() == urlparse(self.authentication.public_origin).netloc)

    def session(self):
        return self.authentication.session(self.headers.get('Cookie'), self.public_request()) if self.authentication and self.authentication.enabled else None

    def require_login(self, path):
        if not self.authentication or not self.authentication.enabled or self.session():
            return True
        if path == '/':
            self.send(303, '', 'text/plain', headers={'Location': '/login'})
        else:
            self.send(401, {'error': '请先登录交易手记。', 'code': 'LOGIN_REQUIRED'})
        return False

    def send(self, status, data, content_type="application/json; charset=utf-8", attachment=None, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.public_request():
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="' + attachment + '"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, {"error": "访问地址未授权。"})
        route = urlparse(self.path)
        try:
            public_files = {'/login': 'login.html', '/login.js': 'login.js', '/login.css': 'login.css', '/sw.js': 'sw.js'}
            if route.path in public_files:
                path = ROOT / 'static' / public_files[route.path]
                return self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0])
            if not self.require_login(route.path):
                return
            if route.path == "/api/state":
                data = journal.state()
                data["stop_coverage"] = stop_coverage.summarize(data, journal.now_ms())
                session = self.session()
                data.update(csrf=session['csrf'] if session else TOKEN, ai=ai_review.status(), credentials_configured=all(credentials(ROOT)),
                            auth={'enabled': bool(self.authentication and self.authentication.enabled), 'username': self.authentication.account['username'] if session else ''})
                return self.send(200, data)
            if route.path == "/api/ai":
                return self.send(200, ai_provider.status())
            if route.path == "/api/v1/training/today":
                return self.send(200, training.today())
            if route.path == "/api/v1/risk-policy":
                return self.send(200, training.get_policy())
            if route.path == "/api/v1/intents":
                return self.send(200, {"items": training.list_intents()})
            if route.path.startswith("/api/v1/intents/"):
                return self.send(200, training.get_intent(route.path.rsplit("/", 1)[1]))
            if route.path.startswith(("/api/v1/strategies", "/api/v1/backtests", "/api/v1/chart-hypothesis")):
                return self.send(410, {"error": "策略实验与历史回测已移除，历史资料仍保存在备份中。"})
            if route.path == "/api/export":
                kind = parse_qs(route.query).get("format", ["json"])[0]
                data = journal.state()
                if kind == "csv":
                    buf = io.StringIO()
                    columns = ["symbol", "id", "orderId", "time", "side", "positionSide", "price", "qty", "realizedPnl", "commission", "commissionAsset"]
                    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(data["trades"])
                    return self.send(200, "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8", "binance-trades.csv")
                # Include full archival history, not only the dashboard's current time window.
                with journal.connect() as db:
                    data["trades"] = [json.loads(r[0]) for r in db.execute("SELECT raw FROM trades ORDER BY time")]
                    data["income"] = [json.loads(r[0]) for r in db.execute("SELECT raw FROM income ORDER BY time")]
                    data["revisions"] = [dict(journal_id=r[0], time=r[1], data=json.loads(r[2])) for r in db.execute("SELECT journal_id,time,data FROM revisions")]
                    data["analyses"] = [dict(id=r[0],journal_id=r[1],created=r[2],status=r[3],data=json.loads(r[4])) for r in db.execute("SELECT * FROM analyses")]
                    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    for table in tables:
                        if table in {"kv", "trades", "income", "revisions", "analyses", "journals", "sqlite_sequence"}:
                            continue
                        data[table] = [dict(r) for r in db.execute("SELECT * FROM " + table)]
                    data["risk_policy"] = training.get_policy()
                return self.send(200, data, attachment="trade-journal.json")
            path = (ROOT/"static"/("index.html" if route.path == "/" else route.path.lstrip("/"))).resolve()
            if not path.is_relative_to(ROOT/"static") or not path.is_file():
                return self.send(404, {"error": "页面不存在。"})
            return self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        except Exception:
            return self.send(500, {"error": "读取失败，请重启本地服务。"})

    def do_PUT(self):
        if urlparse(self.path).path != "/api/v1/risk-policy":
            return self.send(405, {"error": "此接口不支持 PUT。"})
        self.do_POST()

    def do_POST(self):
        expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.authentication and self.authentication.public_origin:
            expected.add(self.authentication.public_origin)
        if not self.allowed_host() or self.headers.get("Origin") not in expected:
            return self.send(403, {"error": "请求来源未授权。", "code": "ORIGIN_REJECTED"})
        path = urlparse(self.path).path
        if path == '/api/login':
            if not self.authentication or not self.authentication.enabled:
                return self.send(409, {'error': '尚未配置登录账号。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError()
                token, status = self.authentication.login(payload.get('username'), payload.get('password'), self.client_address[0])
            except (ValueError, TypeError):
                return self.send(400, {'error': '登录信息格式不正确。'})
            if not token:
                return self.send(status, {'error': '尝试次数较多，请一分钟后再试。' if status == 429 else '账号或密码不正确。'}, headers={'Retry-After': '60'} if status == 429 else None)
            return self.send(200, {'ok': True}, headers={'Set-Cookie': self.authentication.cookie(token, self.public_request())})
        if not self.require_login(path):
            return
        session = self.session()
        expected_token = session['csrf'] if session else TOKEN
        if not secrets.compare_digest(self.headers.get("X-Journal-Token", ''), expected_token):
            return self.send(403, {"error": "连接已更新，正在恢复会话。", "code": "SESSION_EXPIRED"})
        try:
            if path == '/api/logout' and self.authentication and self.authentication.enabled:
                self.authentication.logout(self.headers.get('Cookie'), self.public_request())
                return self.send(200, {'ok': True}, headers={'Set-Cookie': self.authentication.cookie('', self.public_request(), clear=True)})
            length = int(self.headers.get("Content-Length", "0"))
            limit = 7300000 if path == "/api/v1/chart-hypothesis" else 100000
            if not 0 < length <= limit:
                raise ValueError("请求大小无效。")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效。")
            if path == "/api/v1/risk-policy":
                return self.send(200, training.save_policy(payload))
            if path == "/api/v1/training/activity":
                return self.send(200, training.save_activity(payload))
            if path == "/api/v1/intents":
                return self.send(200, training.save_intent(payload))
            if path.startswith("/api/v1/intents/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 5:
                    raise ValueError("计划操作路径不正确。")
                intent_id, action = pieces[3:]
                if action == "review":
                    return self.send(202, pretrade_ai.start_review(intent_id, payload))
                if action == "confirm":
                    intent = training.get_intent(intent_id)
                    evidence = pretrade_evidence.collect(intent)
                    return self.send(200, training.confirm_intent(intent_id, payload, evidence))
                if action == "cancel":
                    return self.send(200, training.cancel_intent(intent_id, payload))
                raise ValueError("不支持此计划操作。")
            if path.startswith(("/api/v1/strategies", "/api/v1/backtests", "/api/v1/chart-hypothesis")):
                return self.send(410, {"error": "策略实验与历史回测已移除，不再创建实验。"})
            if path == "/api/journals":
                return self.send(200, journal.save_journal(payload))
            if path == "/api/recorder":
                current = {**recorder.DEFAULTS, **journal.kv("recorder_settings", {})}
                if "interval" in payload:
                    if payload["interval"] not in (15, 30, 60):
                        raise ValueError("快照间隔可选择 15、30 或 60 秒。")
                    current["interval"] = payload["interval"]
                for key in ("enabled", "auto_ai"):
                    if key in payload:
                        if not isinstance(payload[key], bool):
                            raise ValueError("开关格式不正确。")
                        current[key] = payload[key]
                with journal.connect() as db:
                    journal.set_kv(db, "recorder_settings", current)
                return self.send(200, current)
            if path == "/api/sync":
                days = int(payload.get("days", 30))
                if days not in {7, 30, 90}:
                    raise ValueError("请选择 7、30 或 90 天。")
                if SYNC_LOCK.locked():
                    raise ValueError("正在同步，请等待完成。")
                threading.Thread(target=sync_account, args=(days,), daemon=True).start()
                return self.send(202, {"status": "started"})
            if path == "/api/analyze":
                return self.send(202, {"id": ai_review.start(payload.get("id"))})
            if path == "/api/ai/config":
                if not ai_review.LOCK.acquire(blocking=False):
                    raise ValueError("AI 分析正在运行，完成后再修改连接。")
                try:
                    return self.send(200, ai_provider.save(payload))
                finally:
                    ai_review.LOCK.release()
            if path == "/api/ai/models":
                return self.send(200, ai_provider.models(payload))
            if path == "/api/ai/check":
                return self.send(200, ai_provider.check(payload))
            if path == "/api/credentials":
                key, secret = str(payload.get("key", "")).strip(), str(payload.get("secret", "")).strip()
                if not (16 <= len(key) <= 256 and 16 <= len(secret) <= 256 and key.isalnum() and secret.isalnum()):
                    raise ValueError("API Key 和 Secret 格式不正确。")
                temp = ROOT/".env.local.tmp"
                fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(f"BINANCE_API_KEY={key}\nBINANCE_API_SECRET={secret}\n")
                temp.chmod(0o600)
                os.replace(temp, ROOT/".env.local")
                return self.send(200, {"saved": True})
            return self.send(404, {"error": "接口不存在。"})
        except training.Conflict as e:
            return self.send(409, {"error": str(e), "code": "VERSION_CONFLICT"})
        except journal.JournalConflict as e:
            return self.send(409, {"error": str(e), "code": "JOURNAL_CONFLICT"})
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        except Exception:
            return self.send(500, {"error": "操作失败，原有记录已保留。"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--public-origin', default=os.environ.get('JOURNAL_PUBLIC_ORIGIN', ''))
    parser.add_argument('--auth-file', default=str(ROOT / 'data' / 'auth.json'))
    args = parser.parse_args()
    try:
        authentication = auth.Auth(args.auth_file, args.public_origin)
        if args.host not in ('127.0.0.1', 'localhost') and not (authentication.enabled and authentication.public_origin):
            raise ValueError('监听公网必须配置 HTTPS 公网地址和登录账号。')
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    journal.DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(journal.DB.with_suffix('.server.lock'), 'a')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        parser.error("这份交易账本已有服务在运行，请直接打开原页面。")
    server, worker = None, None
    try:
        # Own the database and port before marking any previous tasks interrupted.
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.auth = authentication
        journal.init()
        training.init()
        pretrade_ai.init()
        with journal.connect() as db:
            db.execute("UPDATE analyses SET status='failed',data=? WHERE status='running'", (json.dumps({"error": "服务已重启，请重新生成复盘。"}, ensure_ascii=False),))
            if journal.kv("sync", {}, db).get("status") == "running":
                journal.set_kv(db, "sync", {"status": "failed", "error": "上次同步被中断，请重新同步。"})
        worker = recorder.Recorder(SYNC_LOCK).start()
        print(f"交易手记已启动：http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()
    finally:
        if worker: worker.stop.set()
        if server: server.server_close()
        lock.close()


if __name__ == "__main__":
    main()
