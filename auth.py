"""Single-owner login. Password hashes live outside static; sessions never leave cookies."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

ITERATIONS = 600_000
SESSION_TTL = 12 * 3600
IDLE_TTL = 2 * 3600


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return {'salt': salt, 'hash': digest, 'iterations': ITERATIONS}


def save_account(path, username, password):
    if not isinstance(username, str) or not username.strip() or len(username) > 80:
        raise ValueError('账号长度应为 1–80 个字符。')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('密码长度应为 12–256 个字符。')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'username': username.strip(), **password_hash(password)}, f)
    temp.chmod(0o600)
    os.replace(temp, path)


def validate_origin(value):
    if not value:
        return ''
    url = urlparse(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('公网地址必须是完整 HTTPS 域名，不含路径。')
    return 'https://' + url.netloc.lower()


class Auth:
    def __init__(self, path, public_origin=''):
        self.path = Path(path)
        self.public_origin = validate_origin(public_origin)
        self.account = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.public_origin and not self.account:
            raise ValueError('公网访问必须先配置登录账号。运行 python3 auth.py。')
        if self.account:
            if not self.account.get('username') or len(bytes.fromhex(self.account['hash'])) != 32 or len(bytes.fromhex(self.account['salt'])) != 16 or self.account['iterations'] != ITERATIONS:
                raise ValueError('登录配置损坏，请重新设置账号。')
        self.sessions = {}
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return self.account is not None

    def cookie_name(self, secure):
        return '__Host-journal' if secure else 'journal_session'

    def token(self, cookie, secure):
        try:
            value = SimpleCookie(cookie or '').get(self.cookie_name(secure))
            return value.value if value else ''
        except Exception:
            return ''

    def session(self, cookie, secure):
        key = hashlib.sha256(self.token(cookie, secure).encode()).hexdigest()
        now = time.monotonic()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if now < v['expires'] and now - v['seen'] < IDLE_TTL}
            session = self.sessions.get(key)
            if session:
                session['seen'] = now
                return dict(session)
        return None

    def login(self, username, password, address):
        now = time.monotonic()
        # An account-wide ceiling also bounds expensive hashing and spoofed proxy IPs.
        with self.lock:
            for key in list(self.attempts):
                while self.attempts[key] and now - self.attempts[key][0] > 60:
                    self.attempts[key].popleft()
                if not self.attempts[key]:
                    del self.attempts[key]
            if len(self.attempts[address]) >= 5 or len(self.attempts['*']) >= 20:
                return None, 429
            self.attempts[address].append(now)
            self.attempts['*'].append(now)
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256 or len(username) > 80:
            return None, 401
        candidate = password_hash(password, self.account['salt'])['hash']
        valid = hmac.compare_digest(candidate, self.account['hash']) & hmac.compare_digest(username.encode(), self.account['username'].encode())
        if not valid:
            return None, 401
        token = secrets.token_urlsafe(32)
        session = {'csrf': secrets.token_urlsafe(32), 'expires': now + SESSION_TTL, 'seen': now}
        with self.lock:
            # Expire oldest sessions when many devices sign in.
            if len(self.sessions) >= 20:
                del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]['seen'])]
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
        return token, 200

    def logout(self, cookie, secure):
        with self.lock:
            self.sessions.pop(hashlib.sha256(self.token(cookie, secure).encode()).hexdigest(), None)

    def cookie(self, token, secure, clear=False):
        return f'{self.cookie_name(secure)}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else SESSION_TTL}' + ('; Secure' if secure else '')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='设置或重置交易手记登录账号；重启服务后生效。')
    parser.add_argument('--file', type=Path, default=Path(__file__).parent / 'data' / 'auth.json')
    parser.add_argument('--username', default='admin')
    args = parser.parse_args()
    password = getpass.getpass('新密码（至少 12 位）: ')
    if password != getpass.getpass('再次输入: '):
        parser.error('两次密码不一致。')
    save_account(args.file, args.username, password)
    print('登录账号已保存。请重启服务，旧会话将失效。')
