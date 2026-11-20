"""core/google_login.py — "Sign in with Google", used only to check the email.

The app asks Google for the account's email address and nothing else, and
keeps no token: the session cookie is issued by web/server.py once the email
matches ZZZMEMO_GOOGLE_EMAIL.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
CLIENT_SECRETS_FILE = BASE_DIR / "data" / "client_secret.json"
if not CLIENT_SECRETS_FILE.exists():  # local development
    CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/api/auth/callback")

_pending_flow = None  # keeps the PKCE code verifier between the two steps


def get_auth_url() -> str:
    global _pending_flow
    if not CLIENT_SECRETS_FILE.exists():
        raise FileNotFoundError("client_secret.json is missing (set GOOGLE_CLIENT_SECRET_JSON)")
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    url, _ = flow.authorization_url(access_type="online", prompt="select_account")
    _pending_flow = flow
    return url


def email_from_callback(authorization_response: str) -> str | None:
    """Finish the OAuth flow and return the signed-in account's email."""
    global _pending_flow
    if _pending_flow is None:
        raise RuntimeError("no login in progress; start again")
    flow, _pending_flow = _pending_flow, None
    # Fly.io terminates TLS, so the app sees http:// callback URLs
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    # Google may report the scopes in a different form than requested
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow.fetch_token(authorization_response=authorization_response)
    info = flow.authorized_session().get("https://openidconnect.googleapis.com/v1/userinfo").json()
    return info.get("email") if info.get("email_verified", True) else None
