"""Bind one Binance account per installation, including credential rotation."""
import os
import tempfile
import journal
from binance_client import Binance, BinanceError, credentials


def bind(client):
    identity = client.identity()
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        bound = journal.kv('exchange:binance:identity', None, db)
        if bound and bound != identity:
            raise BinanceError('此账本已绑定另一 Binance 账户，已停止同步，避免混账。')
        journal.set_kv(db, 'exchange:binance:identity', identity)
    return identity


def save(root, key, secret, lock):
    if not lock.acquire(blocking=False):
        raise ValueError('Binance 正在同步，完成后再更新连接。')
    try:
        # Bind the existing credentials before accepting a replacement on legacy installs.
        if not journal.kv('exchange:binance:identity') and all(credentials(root)):
            existing = Binance(*credentials(root)); existing.align_time(); bind(existing)
        client = Binance(key, secret); client.align_time(); bind(client)
        fd, path = tempfile.mkstemp(prefix='.binance-', dir=root)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(f'BINANCE_API_KEY={key}\nBINANCE_API_SECRET={secret}\n')
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(path, 0o600)
            os.replace(path, root/'.env.local')
        finally:
            if os.path.exists(path): os.unlink(path)
    except BinanceError as exc:
        raise ValueError(str(exc)) from None
    finally:
        lock.release()
