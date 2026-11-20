"""web/deps.py — authentication settings and session helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets

from fastapi import Request

# ── セッション認証 ────────────────────────────────────────────────────────────

_AUTH_USER = os.environ.get("ZZZMEMO_USER", "")
_AUTH_PASS = os.environ.get("ZZZMEMO_PASS", "")
_AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)
INTERNAL_API_KEY = os.environ.get("ZZZMEMO_API_KEY", "")
GOOGLE_EMAIL = os.environ.get("ZZZMEMO_GOOGLE_EMAIL", "")
SESSION_COOKIE = "zzzmemo_session"
SESSION_MAX_AGE = 90 * 24 * 3600  # 90日

# 認証が何も設定されていない = ローカル開発モード（認証スキップ）
AUTH_REQUIRED = _AUTH_ENABLED or bool(GOOGLE_EMAIL)

# 認証なしを明示的に許可するための脱出口（外部公開アドレスで使う場合のみ必要）
_ALLOW_NO_AUTH = os.environ.get("ZZZMEMO_ALLOW_NO_AUTH", "") == "1"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def assert_auth_configured() -> None:
    """認証なしのまま外部到達可能なアドレスで待ち受けるのを起動時に止める。

    危険なのは資格情報が無いことではなく、認証が無いまま露出することなので、
    bind 先が loopback かどうかで判定する。ローカル単体利用は従来どおり
    認証なしで動く。設定漏れが「静かな全公開」ではなく起動失敗として出る。
    """
    if AUTH_REQUIRED or _ALLOW_NO_AUTH:
        return
    host = os.environ.get("HOST", "127.0.0.1")
    if host in _LOOPBACK:
        return
    raise RuntimeError(
        f"認証が未設定のまま HOST={host} で待ち受けようとしました。"
        "ZZZMEMO_USER/ZZZMEMO_PASS か ZZZMEMO_GOOGLE_EMAIL を設定してください。"
        "認証なしで公開するのが意図どおりなら ZZZMEMO_ALLOW_NO_AUTH=1 を設定してください。"
    )


def make_session_token() -> str:
    """認証情報から決定論的なセッショントークンを生成（ストレージ不要）。"""
    if _AUTH_ENABLED:
        key = f"{_AUTH_USER}:{_AUTH_PASS}".encode()
    else:
        key = GOOGLE_EMAIL.encode() if GOOGLE_EMAIL else b"zzzmemo-open"
    return hmac.new(key, b"zzzmemo-session-v1", hashlib.sha256).hexdigest()


def verify_session(request: Request) -> bool:
    if not AUTH_REQUIRED:
        return True
    if INTERNAL_API_KEY:
        api_key = request.headers.get("X-Api-Key", "")
        if api_key and _secrets.compare_digest(api_key, INTERNAL_API_KEY):
            return True
    token = request.cookies.get(SESSION_COOKIE, "")
    return _secrets.compare_digest(token, make_session_token())
