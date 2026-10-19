"""The trader's own versioned rulebook. Only the trader confirms or changes it."""
import json
import uuid

import journal

DEFAULT_SYSTEM = {
    'rules': {'min_rr': 1.5, 'account_risk_pct': 2, 'trial_fraction': 0.5, 'confirmed': False},
    'setups': [
        dict(id='support_bounce', name='支撑反弹', summary='回踩到摆动低点附近，收盘收回后跟进。',
             direction_hint='LONG', default_timeframe='5m',
             position=['near_support'], trigger=['reclaim_zone'],
             bonus=['reversal_candle', 'rsi_divergence', 'macd_turn', 'higher_tf_trend', 'volume_expansion'],
             stop_rule='beyond_swing_atr', target_rule='swing_or_min_rr', hold_hint='数根到一两个小时', status='active'),
        dict(id='strength_follow', name='强势跟随', summary='比 BTC 强的币回踩 EMA20，重新站上时顺势跟进。',
             direction_hint='LONG', default_timeframe='15m',
             position=['pullback_ema20'], trigger=['break_prev_high'],
             bonus=['higher_tf_trend', 'relative_strength', 'volume_expansion', 'oi_rising_with_buyers'],
             stop_rule='beyond_recent_atr', target_rule='rr_2', hold_hint='一到数小时', status='active'),
        dict(id='exhaustion_reversal', name='跌到位抄底', summary='RSI 超卖到摆动低点附近，反转 K 线被突破才进（逆势，小仓）。',
             direction_hint='LONG', default_timeframe='15m',
             position=['oversold_at_low'], trigger=['reversal_then_break'],
             bonus=['rsi_divergence', 'macd_turn', 'volume_expansion', 'seller_exhaustion'],
             stop_rule='beyond_recent_atr', target_rule='swing_or_min_rr', hold_hint='一两个小时内看到反弹', status='active'),
        dict(id='discretionary', name='盘感试单', summary='说不清技术理由但想试：写下押什么、多久没反应就走，只开半仓。',
             direction_hint='', default_timeframe='5m', position=[], trigger=[], bonus=[],
             stop_rule='user', target_rule='user', hold_hint='按你写的时限', status='active'),
    ]}


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS trading_system_versions(id TEXT PRIMARY KEY,version INTEGER NOT NULL,created INTEGER NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL)')


def _row(r):
    return dict(id=r['id'], version=r['version'], created=r['created'], status=r['status'], data=json.loads(r['data']))


def active(db=None):
    if db is None:
        with journal.connect() as conn:
            return active(conn)
    row = db.execute("SELECT * FROM trading_system_versions WHERE status='active' ORDER BY version DESC LIMIT 1").fetchone()
    if row:
        return _row(row)
    if not db.execute('SELECT 1 FROM trading_system_versions').fetchone():
        db.execute('INSERT INTO trading_system_versions VALUES(?,?,?,?,?)',
                   (uuid.uuid4().hex[:12], 1, journal.now_ms(), 'active', json.dumps(DEFAULT_SYSTEM, ensure_ascii=False)))
        return active(db)
    raise ValueError('交易系统没有生效版本。')


def _number(payload, key, lo, hi):
    v = payload[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f'{key} 需要在 {lo}–{hi} 之间。')
    return v


def confirm(payload):
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = active(db)
        data = json.loads(json.dumps(current['data']))
        if 'account_risk_pct' in payload:
            data['rules']['account_risk_pct'] = _number(payload, 'account_risk_pct', 0.1, 10)
        if 'min_rr' in payload:
            data['rules']['min_rr'] = _number(payload, 'min_rr', 1, 5)
        data['rules']['confirmed'] = True
        db.execute("UPDATE trading_system_versions SET status='superseded' WHERE id=?", (current['id'],))
        identity = uuid.uuid4().hex[:12]
        db.execute('INSERT INTO trading_system_versions VALUES(?,?,?,?,?)',
                   (identity, current['version']+1, max(journal.now_ms(), current['created']+1), 'active', json.dumps(data, ensure_ascii=False)))
        return active(db)


def history():
    with journal.connect() as db:
        active(db)
        return [_row(r) for r in db.execute('SELECT * FROM trading_system_versions ORDER BY version DESC')]
