"""Bearer-token auth for the RTIA API + Gradio mount.

Jupyter-style: a fresh URL-safe token is generated per process at startup
unless ``RTIA_API_TOKEN`` is set in the environment (stable token across
restarts is useful when iterating locally with a hot browser tab).

Two surfaces accept the token:

* All API endpoints require ``Authorization: Bearer <token>``. Missing or
  wrong header → 401.
* The Gradio UI mount additionally accepts ``?token=<token>`` so the
  startup-printed URL is a one-click open. This is implemented as a
  middleware that rewrites ``?token=…`` requests into bearer-equivalent
  ones before they reach Gradio's handlers (Gradio has no native bearer
  support and we don't want to fork it).

v1 caveat: this is single-token, single-tenant, localhost-by-default.
JWT/OAuth, rotation, and per-user threads are post-v1 hardening.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_TOKEN_ENV_VAR = "RTIA_API_TOKEN"
_QUERY_PARAM = "token"
_COOKIE_NAME = "rtia_token"

_bearer = HTTPBearer(auto_error=False)


def generate_token() -> str:
    """Return ``RTIA_API_TOKEN`` if set; otherwise a fresh URL-safe token.

    32 bytes of entropy → ~43 characters of base64url. Plenty for a
    localhost-only deployment, easy to copy/paste into a curl flag.
    """
    explicit = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if explicit:
        return explicit
    return secrets.token_urlsafe(32)


class TokenStore:
    """Single-token store, set at app startup and read by the dependency.

    A class (rather than a module-level global) so tests can construct
    isolated instances without monkeypatching module state. The FastAPI
    app holds one instance on ``app.state``.
    """

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    def verify(self, candidate: str | None) -> bool:
        """Constant-time compare so a timing attack can't probe the token."""
        if not candidate:
            return False
        return secrets.compare_digest(self._token, candidate)


def verify_token(request: Request) -> None:
    """FastAPI dependency: accepts ``Authorization: Bearer …`` OR ``?token=…`` OR cookie.

    Three acceptance paths, tried in order:

    1. ``Authorization: Bearer <token>`` — canonical; curl + API clients.
    2. ``?token=<token>`` query param — one-click open from the printed
       startup URL; occasionally convenient for curl smoke.
    3. ``rtia_token`` cookie — set by the UI auth middleware after a
       successful #1 or #2 so in-browser fetches from the Gradio JS
       bundle to ``/pipeline*`` / ``/uploads/*`` authenticate without
       the JS having to handle the token.
    """
    store: TokenStore = request.app.state.token_store

    # Header path (canonical).
    creds: HTTPAuthorizationCredentials | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=auth_header.split(" ", 1)[1].strip()
        )
    if creds and store.verify(creds.credentials):
        return

    # Query-param fallback.
    qp = request.query_params.get(_QUERY_PARAM)
    if qp and store.verify(qp):
        return

    # Cookie fallback (set by the UI middleware after the initial
    # tokenized navigation).
    cookie = request.cookies.get(_COOKIE_NAME)
    if cookie and store.verify(cookie):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


__all__ = ["TokenStore", "generate_token", "verify_token"]
