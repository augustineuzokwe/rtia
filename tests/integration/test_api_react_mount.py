"""Mount-layout tests for Epic 6 / US-16.

After the React scaffold landed, ``/`` serves the SPA's static build and
the Gradio UI moves to ``/legacy``. These tests don't exercise the real
Gradio mount (it's stubbed out by ``create_app`` without it for unit
tests), so we test the two layers that matter at the API level:

1. The fallback-when-missing behaviour - clear 500 with a build hint
   instead of a stack trace, gated by the same bearer token as the rest
   of the UI.
2. When a build exists, ``GET /`` returns the SPA's ``index.html``.

The build is faked with a tmp dir + monkeypatch on ``api.main._REACT_DIST``
so the test stays hermetic (no ``npm run build`` in pytest).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app

TOKEN = "tok"


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _client(monkeypatch: pytest.MonkeyPatch, dist: Path) -> TestClient:
    """Build an app with ``_REACT_DIST`` pointed at ``dist`` (built or empty)."""
    monkeypatch.setattr(api_main, "_REACT_DIST", dist)
    app = create_app(runner=MagicMock(), token=TOKEN)
    return TestClient(app)


def test_root_returns_500_with_build_hint_when_dist_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dist on disk → friendly 500, not a Starlette traceback."""
    client = _client(monkeypatch, tmp_path / "nonexistent")
    r = client.get("/", headers=_auth())
    assert r.status_code == 500
    body = r.text
    assert "npm install" in body
    assert "npm run build" in body
    # Sanity: not a stack trace.
    assert "Traceback" not in body


def test_root_serves_react_index_when_dist_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dist with index.html → 200 + HTML body served from the build."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>RTIA</title></head>"
        "<body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    client = _client(monkeypatch, dist)
    r = client.get("/", headers=_auth())
    assert r.status_code == 200
    assert "<div id='root'></div>" in r.text
    # The static mount should advertise HTML.
    assert "text/html" in r.headers.get("content-type", "")


def test_react_static_assets_are_token_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asset fetches without the bearer token must 401, matching the
    Gradio-era behaviour - the SPA's JS/CSS are part of the gated UI."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log('rtia');", encoding="utf-8")
    client = _client(monkeypatch, dist)
    unauth = client.get("/assets/index-abc.js")
    assert unauth.status_code == 401
    authed = client.get("/assets/index-abc.js", headers=_auth())
    assert authed.status_code == 200
    assert "rtia" in authed.text


def test_api_routes_still_work_with_react_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The static SPA at ``/`` must not shadow ``/pipeline`` etc."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(api_main, "_REACT_DIST", dist)
    runner = MagicMock()
    runner.start.side_effect = AssertionError("start() shouldn't run for this validation test")
    app = create_app(runner=runner, token=TOKEN)
    client = TestClient(app)

    # 422 from Pydantic validation proves the route resolved (not 404
    # from the static mount). Empty text trips ``min_length=1``.
    r = client.post("/pipeline", headers=_auth(), json={"requirement_text": ""})
    assert r.status_code == 422
