"""Tests for FastAPI endpoints via TestClient with a mocked runner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.models import ThreadState, ThreadStatus

TOKEN = "tok"


@pytest.fixture
def runner_mock():
    return MagicMock()


@pytest.fixture
def client(runner_mock):
    app = create_app(runner=runner_mock, token=TOKEN)
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_post_pipeline_returns_thread_state(client, runner_mock):
    runner_mock.start.return_value = ThreadState(
        thread_id="tid-1",
        status=ThreadStatus.PAUSED_REVIEW,
        payload={"rendered_artifact": "## Description\n..."},
    )
    r = client.post("/pipeline", headers=_auth(), json={"requirement_text": "some req"})
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] == "tid-1"
    assert body["status"] == "paused_review"


def test_post_pipeline_requires_non_empty_text(client):
    r = client.post("/pipeline", headers=_auth(), json={"requirement_text": ""})
    assert r.status_code == 422


def test_post_pipeline_rejects_whitespace_only_text(client):
    """Epic #1 intake-soundness audit (#1) - the UI's ``on_run`` rejects
    whitespace-only input client-side, but a direct API caller could
    otherwise slip a blank ``"   "`` past the Pydantic ``min_length=1``
    check and start an empty pipeline run that the Analyst then errors
    on confusingly. The ``_reject_blank`` validator on
    ``PipelineRequest`` is the API-layer guardrail."""
    for blank in ["   ", "\n\n", "\t  \n", " \r\n "]:
        r = client.post("/pipeline", headers=_auth(), json={"requirement_text": blank})
        assert r.status_code == 422, f"expected 422 for {blank!r}, got {r.status_code}"


def test_get_pipeline_state(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE, payload={"final_artifact": {}}
    )
    r = client.get("/pipeline/tid", headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_resume_routes_po_answers(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={"critical_ambiguities": ["Q?"]},
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_REVIEW,
        payload={"rendered_artifact": "..."},
    )
    r = client.post(
        "/pipeline/tid/resume",
        headers=_auth(),
        json={"answers": {"Q?": "A!"}},
    )
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with("tid", {"Q?": "A!"})


def test_resume_review_accept(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_REVIEW,
        payload={"rendered_artifact": "..."},
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE, payload={}
    )
    r = client.post("/pipeline/tid/resume", headers=_auth(), json={"accepted": True})
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with("tid", {"accepted": True})


def test_resume_review_override(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_REVIEW,
        payload={"rendered_artifact": "..."},
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE, payload={}
    )
    r = client.post(
        "/pipeline/tid/resume",
        headers=_auth(),
        json={"accepted": False, "description": "new d", "objective": "new o"},
    )
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with(
        "tid",
        {"accepted": False, "description": "new d", "objective": "new o"},
    )


def test_resume_rejects_when_thread_not_paused(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE, payload={}
    )
    r = client.post("/pipeline/tid/resume", headers=_auth(), json={"accepted": True})
    assert r.status_code == 409


def test_resume_po_requires_answers_field(client, runner_mock):
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={"critical_ambiguities": ["Q?"]},
    )
    r = client.post("/pipeline/tid/resume", headers=_auth(), json={"accepted": True})
    assert r.status_code == 400


def test_export_markdown_returns_404_before_composer(client, runner_mock):
    runner_mock.render_markdown.return_value = None
    r = client.get("/pipeline/tid/export.md", headers=_auth())
    assert r.status_code == 404


def test_export_markdown_returns_markdown(client, runner_mock):
    runner_mock.render_markdown.return_value = "## Description\nbody"
    r = client.get("/pipeline/tid/export.md", headers=_auth())
    assert r.status_code == 200
    assert r.text == "## Description\nbody"
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]


def test_upload_pdf_rejects_scanned(client):
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    r = client.post(
        "/uploads/pdf",
        headers=_auth(),
        files={"file": ("blank.pdf", buf.getvalue(), "application/pdf")},
    )
    assert r.status_code == 422
    body = r.json()
    assert "scanned_pdf" in str(body["detail"]) or "scanned" in str(body["detail"]).lower()


def test_upload_markdown_roundtrip(client):
    r = client.post(
        "/uploads/markdown",
        headers=_auth(),
        files={"file": ("req.md", b"# Req\n\nAs a user...", "text/markdown")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"].startswith("# Req")
    assert body["char_count"] == len(body["text"])


def test_resume_routes_split_resume_shape(client, runner_mock):
    """resume with selected_story_titles → structured payload."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={
            "mode": "split",
            "implied_stories": [
                {"title": "Story A", "summary": "a"},
                {"title": "Story B", "summary": "b"},
            ],
            "critical_ambiguities": [],
        },
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={"split_stories": [{"title": "Story A", "summary": "a"}]},
    )
    r = client.post(
        "/pipeline/tid/resume",
        headers=_auth(),
        json={"selected_story_titles": ["Story A"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done_split"
    runner_mock.resume.assert_called_once_with(
        "tid", {"selected_story_titles": ["Story A"], "answers": {}}
    )


def test_resume_split_empty_selection_passes_empty_list(client, runner_mock):
    """Q2 - None or empty selected_story_titles passes [] through."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={
            "mode": "split",
            "implied_stories": [{"title": "A", "summary": "a"}],
            "critical_ambiguities": [],
        },
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE_SPLIT, payload={}
    )
    r = client.post("/pipeline/tid/resume", headers=_auth(), json={})
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with("tid", {"selected_story_titles": [], "answers": {}})


def test_resume_deep_mode_still_requires_answers(client, runner_mock):
    """deep-mode PO checkpoint contract unchanged."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={"mode": "deep", "critical_ambiguities": ["Q?"]},
    )
    r = client.post("/pipeline/tid/resume", headers=_auth(), json={"accepted": True})
    assert r.status_code == 400


def test_resume_split_prefers_selected_stories_over_legacy_titles(client, runner_mock):
    """Issue #207 - preferred shape ``selected_stories`` (editable
    titles + summaries) wins over legacy ``selected_story_titles`` and
    is forwarded to the runner verbatim."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={
            "mode": "split",
            "implied_stories": [
                {"title": "Story A", "summary": "a"},
                {"title": "Story B", "summary": "b"},
            ],
            "critical_ambiguities": [],
        },
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={"split_stories": [{"title": "Story A renamed", "summary": "a"}]},
    )
    r = client.post(
        "/pipeline/tid/resume",
        headers=_auth(),
        json={
            "selected_stories": [
                {
                    "title": "Story A renamed",
                    "summary": "a",
                    "original_title": "Story A",
                }
            ],
            # Legacy field also supplied - must be ignored when
            # ``selected_stories`` is present.
            "selected_story_titles": ["Story B"],
        },
    )
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with(
        "tid",
        {
            "selected_stories": [
                {
                    "title": "Story A renamed",
                    "summary": "a",
                    "original_title": "Story A",
                }
            ],
            "answers": {},
        },
    )


def test_resume_split_legacy_titles_still_accepted_when_no_selected_stories(client, runner_mock):
    """Issue #207 - back-compat. When ``selected_stories`` is absent
    but legacy ``selected_story_titles`` is present, the legacy shape
    is forwarded unchanged so older API callers keep working."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.PAUSED_PO,
        payload={
            "mode": "split",
            "implied_stories": [{"title": "Story A", "summary": "a"}],
            "critical_ambiguities": [],
        },
    )
    runner_mock.resume.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE_SPLIT, payload={}
    )
    r = client.post(
        "/pipeline/tid/resume",
        headers=_auth(),
        json={"selected_story_titles": ["Story A"]},
    )
    assert r.status_code == 200
    runner_mock.resume.assert_called_once_with(
        "tid", {"selected_story_titles": ["Story A"], "answers": {}}
    )
