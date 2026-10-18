import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import auth
import server


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'auth.json'
        auth.save_account(self.path, 'test-user', 'fixture-password-123')
        self.auth = auth.Auth(self.path, 'https://trader.example.com')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http.auth = self.auth
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, path, payload=None, cookie='', csrf='', public=False, origin=None, host=None):
        headers = {'Cookie': cookie, 'X-Journal-Token': csrf, 'Content-Type': 'application/json',
                   'Origin': origin or ('https://trader.example.com' if public else self.url)}
        if public: headers['Host'] = 'trader.example.com'
        if host: headers['Host'] = host
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            result = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            result = e
        with result:
            data = result.read()
            return result.status, result.headers, json.loads(data) if result.headers['Content-Type'].startswith('application/json') else data

    def login(self, **kwargs):
        status, headers, _ = self.request('/api/login', {'username': 'test-user', 'password': 'fixture-password-123'}, **kwargs)
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_all_private_routes_are_gated_before_read_or_write(self):
        for path in ['/api/review/daily', '/daily-review.js', '/api/review/learning', '/learning.js', '/api/state', '/api/ai', '/api/export', '/api/v1/intents', '/assets/private-attachment.png', '/index.html']:
            self.assertEqual(self.request(path)[0], 401, path)
        with patch.object(server.journal, 'save_journal') as save:
            self.assertEqual(self.request('/api/journals', {'id': 'private'}, csrf=server.TOKEN)[0], 401)
            save.assert_not_called()
        for route in ['/api/review/retry', '/api/review/actions', '/api/review/actions/archive', '/api/review/practices', '/api/review/practices/undo', '/api/ai/config', '/api/ai/check', '/api/ai/models']:
            self.assertEqual(self.request(route, {'api_key':'sk-fixture'}, csrf=server.TOKEN)[0], 401)
        self.assertEqual(self.request('/login')[0], 200)

    def test_login_csrf_logout_and_cookie_security(self):
        cookie = self.login(public=True)
        session = self.auth.session(cookie, True)
        self.assertNotEqual(session['csrf'], server.TOKEN)
        with patch.object(server.journal, 'save_journal', return_value={'saved': True}) as save:
            self.assertEqual(self.request('/api/journals', {}, cookie, server.TOKEN, public=True)[0], 403)
            save.assert_not_called()
            self.assertEqual(self.request('/api/journals', {}, cookie, session['csrf'], public=True)[0], 200)
        status, headers, _ = self.request('/api/logout', {}, cookie, session['csrf'], public=True)
        self.assertEqual(status, 200)
        for flag in ['__Host-journal=', 'HttpOnly', 'SameSite=Strict', 'Secure', 'Max-Age=0', 'Path=/']:
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.request('/api/export', cookie=cookie, public=True)[0], 401)

    def test_invalid_password_throttled_and_origin_and_host_rejected(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/login', {'username': 'test-user', 'password': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/login', {'username': 'test-user', 'password': 'fixture-password-123'})[0], 429)
        self.assertEqual(self.request('/api/login', {}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('/login', host='evil.example')[0], 403)

    def test_expiration_restart_and_local_cookie_cannot_access_public_host(self):
        cookie = self.login()
        self.assertEqual(self.request('/api/export', cookie=cookie, public=True)[0], 401)
        self.assertIsNone(auth.Auth(self.path).session(cookie, False))
        with patch.object(auth.time, 'monotonic', return_value=auth.time.monotonic() + auth.IDLE_TTL + 1):
            self.assertEqual(self.request('/api/export', cookie=cookie)[0], 401)

    def test_hash_and_public_config_fail_closed(self):
        self.assertNotIn('fixture-password-123', self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): auth.Auth(self.path.parent / 'missing.json', 'https://trader.example.com')
        for value in ['http://trader.example.com', 'https://user:pass@example.com', 'https://example.com/path']:
            with self.assertRaises(ValueError): auth.validate_origin(value)


if __name__ == '__main__': unittest.main()
