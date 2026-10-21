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
import exchange_sync
import binance_account
import ledger
import training
import pretrade_ai
import pretrade_evidence
import auth
import sharing
import review_learning
import review_automation
import market_replay
import trader_memory
import trade_assistant
import trading_system
import mentor_review
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
        binance_account.bind(client)
        start = end - int(days) * 86400000
        positions = client.get("/fapi/v3/positionRisk", signed=True)
        positions = [p for p in positions if Decimal(p["positionAmt"]) != 0]
        income = client.income(start, end)
        ledger.classified(income, allow_unknown=True)
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
                                 "scope": "Binance USDⓈ-M；USDT 成交，全部账单币种分列", "complete": True, "ledger_version": ledger.VERSION}}
        with journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM income WHERE time BETWEEN ? AND ?", (start, end))
            for t in trades:
                db.execute("INSERT OR REPLACE INTO trades VALUES(?,?,?,?)", (t["symbol"], str(t["id"]), t["time"], json.dumps(t)))
            for item in income:
                db.execute("INSERT OR REPLACE INTO income VALUES(?,?,?,?)", (item["incomeType"], str(item["tranId"]), item["time"], json.dumps(item)))
            ledger.record_coverage(db, "binance", start, end)
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
            public_files.update({'/share-render.js': 'share-render.js', '/share-public.js': 'share-public.js', '/share.css': 'share.css'})
            if route.path in public_files:
                path = ROOT / 'static' / public_files[route.path]
                return self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0])
            if route.path.startswith(('/s/', '/api/public/shares/')):
                prefix = '/s/' if route.path.startswith('/s/') else '/api/public/shares/'
                report = sharing.public_report(route.path[len(prefix):])
                headers = {'X-Robots-Tag': 'noindex, nofollow, noarchive'}
                if report is None:
                    return self.send(404, '分享不存在、已过期或已被撤销。', 'text/plain; charset=utf-8', headers=headers)
                if prefix == '/s/':
                    return self.send(200, (ROOT/'static'/'share-public.html').read_bytes(), 'text/html; charset=utf-8', headers=headers)
                return self.send(200, report, headers=headers)
            if not self.require_login(route.path):
                return
            if route.path == "/api/state":
                data = journal.state(binance_configured=all(credentials(ROOT)))
                data["stop_coverage"] = stop_coverage.summarize(data, journal.now_ms())
                data["cooldown"] = trade_assistant.cooldown()
                session = self.session()
                data.update(csrf=session['csrf'] if session else TOKEN, ai=ai_review.status(), credentials_configured=all(credentials(ROOT)),
                            auth={'enabled': bool(self.authentication and self.authentication.enabled), 'username': self.authentication.account['username'] if session else ''})
                return self.send(200, data)
            if route.path == "/api/portfolio":
                import portfolio
                params = parse_qs(route.query, keep_blank_values=True)
                if set(params) - {"days", "exchanges"} or any(len(v) != 1 for v in params.values()):
                    raise ValueError("合并账本查询参数不正确。")
                days = int(params.get('days', ['30'])[0])
                exchanges = params.get('exchanges', [None])[0]
                with journal.connect() as db:
                    db.execute('BEGIN')
                    data = portfolio.report(db, days, exchanges.split(',') if exchanges is not None else None,
                                            binance_configured=all(credentials(ROOT)))
                return self.send(200, data)
            if route.path == '/api/review/learning':
                query = parse_qs(route.query)
                return self.send(200, review_learning.report(int(query.get('days', ['30'])[0]), query.get('exchange', ['all'])[0]))
            if route.path == '/api/review/daily':
                return self.send(200, review_automation.listing())
            if route.path == '/api/assistant/memory':
                return self.send(200, trader_memory.snapshot())
            if route.path == '/api/assistant/memory.md':
                return self.send(200, trader_memory.markdown(trader_memory.snapshot()), 'text/markdown; charset=utf-8', 'trader-memory.md')
            if route.path == '/api/assistant/cooldown':
                return self.send(200, trade_assistant.cooldown())
            if route.path == '/api/assistant/status':
                return self.send(200, trade_assistant.status())
            if route.path == '/api/mentor/review':
                return self.send(200, mentor_review.get(parse_qs(route.query).get('id',[''])[0]))
            if route.path == '/api/system':
                return self.send(200, {'active':trading_system.active(),'history':trading_system.history()})
            if route.path == '/api/assistant/checks':
                return self.send(200, {'items':trade_assistant.listing()})
            if route.path == '/api/market/replay':
                jid = parse_qs(route.query).get('id',[''])[0]
                return self.send(200, {'replay':market_replay.latest(jid)})
            if route.path == "/api/ai":
                return self.send(200, ai_provider.status())
            if route.path == '/api/shares':
                return self.send(200, sharing.listing())
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
                    columns = ["exchange", "symbol", "id", "orderId", "time", "side", "positionSide", "price", "qty", "realizedPnl", "commission", "commissionAsset"]
                    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(dict(t, exchange="binance") for t in data["trades"])
                    for account in data["exchanges"].values(): writer.writerows(account["trades"])
                    return self.send(200, "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8", "exchange-trades.csv")
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
            review_routes = {'/api/review/actions': review_learning.create_action,
                             '/api/review/actions/archive': review_learning.archive_action,
                             '/api/review/practices': review_learning.practice,
                             '/api/review/practices/undo': review_learning.undo_practice}
            if path in review_routes:
                return self.send(200, review_routes[path](payload))
            if path == "/api/journals":
                return self.send(200, journal.save_journal(payload))
            if path == '/api/assistant/memory':
                return self.send(200, trader_memory.save(payload))
            if path == '/api/assistant/cooldown':
                return self.send(200, trade_assistant.set_cooldown(payload))
            if path == '/api/assistant/position':
                return self.send(200, trade_assistant.resolve_position(payload))
            if path == '/api/mentor/adopt':
                return self.send(200, mentor_review.adopt(payload.get('journal_id')))
            if path == '/api/system/confirm':
                return self.send(200, trading_system.confirm(payload))
            if path == '/api/assistant/check':
                return self.send(200, trade_assistant.create(payload))
            if path == '/api/market/replay':
                return self.send(200, {'replay':market_replay.capture(payload.get('id'),force=True)})
            if path == '/api/review/retry':
                return self.send(200, review_automation.retry_failed())
            if path == '/api/shares/preview':
                return self.send(200, sharing.preview(payload))
            if path == '/api/shares/publish':
                return self.send(200, sharing.publish(payload))
            if path == '/api/shares/revoke':
                return self.send(200, sharing.revoke(payload))
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
            if path.startswith("/api/exchanges/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4: raise ValueError("交易所操作路径不正确。")
                exchange, action = parts[2:]
                exchange_sync.validate_exchange(exchange)
                if action == "credentials": return self.send(200, exchange_sync.save(exchange, payload))
                if action == "sync": return self.send(202, exchange_sync.request_sync(exchange, payload.get("days", 30)))
                if action == "enabled": return self.send(200, exchange_sync.set_enabled(exchange, payload.get("enabled")))
                raise ValueError("不支持此交易所操作。")
            if path == "/api/credentials":
                key, secret = str(payload.get("key", "")).strip(), str(payload.get("secret", "")).strip()
                if not (16 <= len(key) <= 256 and 16 <= len(secret) <= 256 and key.isalnum() and secret.isalnum()):
                    raise ValueError("API Key 和 Secret 格式不正确。")
                binance_account.save(ROOT, key, secret, SYNC_LOCK)
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
    server, worker, review_worker, exchange_workers = None, None, None, []
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
        exchange_workers = exchange_sync.start_workers()
        review_worker = review_automation.Worker().start()
        print(f"交易手记已启动：http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()
    finally:
        for extra in exchange_workers:
            extra.stop.set()
            extra.wake.set()
        if worker: worker.stop.set()
        if review_worker: review_worker.stop.set()
        if server: server.server_close()
        lock.close()


if __name__ == "__main__":
    main()
