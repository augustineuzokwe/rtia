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
    # Two consecutive generations differ — entropy is real, not a constant.
    assert token != generate_token()


def test_token_store_constant_time_compare():
    store = TokenStore("abc")
    assert store.verify("abc")
    assert not store.verify("xyz")
    assert not store.verify(None)
    assert not store.verify("")
