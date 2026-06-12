"""Tests for the bearer-token + ?token= query-param auth dependency."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.auth import TokenStore, generate_token
from api.main import create_app


def _client(token: str = "test-token") -> TestClient:
    runner = MagicMock()
    runner.get_state = MagicMock()
    app = create_app(runner=runner, token=token)
    return TestClient(app)


def test_missing_authorization_returns_401():
    client = _client()
    r = client.get("/pipeline/abc")
    assert r.status_code == 401


def test_wrong_token_returns_401():
    client = _client(token="real-token")
    r = client.get("/pipeline/abc", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_bearer_token_accepted():
    runner = MagicMock()
    state = MagicMock()
    state.model_dump = MagicMock(
        return_value={"thread_id": "abc", "status": "running", "payload": {}}
    )
    # FastAPI serializes via the model; pass an actual ThreadState through runner.
    from api.models import ThreadState, ThreadStatus

    runner.get_state.return_value = ThreadState(
        thread_id="abc", status=ThreadStatus.RUNNING, payload={}
    )
    app = create_app(runner=runner, token="real-token")
    client = TestClient(app)
    r = client.get("/pipeline/abc", headers={"Authorization": "Bearer real-token"})
    assert r.status_code == 200


def test_query_param_token_accepted():
    from api.models import ThreadState, ThreadStatus

    runner = MagicMock()
    runner.get_state.return_value = ThreadState(
        thread_id="abc", status=ThreadStatus.RUNNING, payload={}
    )
    app = create_app(runner=runner, token="qp-token")
    client = TestClient(app)
    r = client.get("/pipeline/abc?token=qp-token")
    assert r.status_code == 200


def test_generate_token_uses_env_override(monkeypatch):
    monkeypatch.setenv("RTIA_API_TOKEN", "pinned-token")
    assert generate_token() == "pinned-token"


def test_generate_token_falls_back_to_random(monkeypatch):
    monkeypatch.delenv("RTIA_API_TOKEN", raising=False)
    token = generate_token()
    assert len(token) >= 32
    # Two consecutive generations differ - entropy is real, not a constant.
    assert token != generate_token()


def test_query_param_sets_session_cookie():
    """First navigation with ``?token=`` sets the ``rtia_token`` cookie.

    Without this, the browser's subsequent asset fetches under the
    Gradio mount drop the query string and get 401'd - the page renders
    blank. The cookie keeps in-browser auth sticky.
    """
    from api.models import ThreadState, ThreadStatus

    runner = MagicMock()
    runner.get_state.return_value = ThreadState(
        thread_id="abc", status=ThreadStatus.RUNNING, payload={}
    )
    app = create_app(runner=runner, token="cookie-token")
    client = TestClient(app)
    # Hit the Gradio mount path (middleware path), not the API route.
    r = client.get("/healthcheck?token=cookie-token", follow_redirects=False)
    # Whatever the underlying response (likely 404 from Gradio because
    # the path doesn't exist), the middleware should have set the cookie
    # because the token was valid.
    cookie = r.cookies.get("rtia_token")
    assert cookie == "cookie-token"


def test_cookie_accepted_by_api_route():
    """After the cookie is set, subsequent API calls authenticate from it."""
    from api.models import ThreadState, ThreadStatus

    runner = MagicMock()
    runner.get_state.return_value = ThreadState(
        thread_id="abc", status=ThreadStatus.RUNNING, payload={}
    )
    app = create_app(runner=runner, token="cookie-token")
    client = TestClient(app)
    client.cookies.set("rtia_token", "cookie-token")
    r = client.get("/pipeline/abc")
    assert r.status_code == 200


def test_token_store_constant_time_compare():
    store = TokenStore("abc")
    assert store.verify("abc")
    assert not store.verify("xyz")
    assert not store.verify(None)
    assert not store.verify("")
