"""FastAPI application factory for RTIA's Phase 14 surface.

Mounts the Gradio UI at ``/`` so a single process + a single token covers
both API and UI. The startup banner prints a tokenized URL the operator
can open directly.

Layered like the demo script:

1. ``configure_logging()`` - structured JSON logs on stderr.
2. ``assert_safe_for_env()`` - refuse to start if ``RTIA_ENV=production``
   AND LangSmith tracing is on (Phase 12.4 guard).
3. Mint the API token (or read ``RTIA_API_TOKEN``).
4. Build the runner (one compiled pipeline per process).
5. Mount routes + Gradio.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.errors import InvalidUpdateError
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from agents._logging import configure_logging
from agents.observability import ProductionTracingError, assert_safe_for_env
from api._shared import build_followup_markdown, select_followup_source
from api.auth import TokenStore, generate_token, verify_token
from api.models import (
    PipelineRequest,
    ResumeRequest,
    ThreadState,
    ThreadStatus,
    UploadResult,
)
from api.parsers import (
    FileTooLargeError,
    ParseError,
    ScannedPdfError,
    extract_markdown,
    extract_pdf,
)
from api.runner import PipelineRunner
from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportRequest,
    ExportResult,
    ExportTarget,
    make_exporter,
)

_log = logging.getLogger("rtia.api")


class DeferredExportRequest(BaseModel):
    """Body shape for ``POST /pipeline/{thread_id}/export-deferred``.

    ``include`` is an optional case-insensitive title filter - when set,
    only deferred stories whose title matches one of the entries are
    exported. Default is "export all".
    """

    target: ExportTarget
    include: list[str] | None = Field(
        default=None,
        description="Optional title filter; omit to export every deferred story.",
    )
    dry_run: bool = False


class DeferredExportResponse(BaseModel):
    results: list[ExportResult]
    skipped: list[str] = Field(
        default_factory=list,
        description="Titles that were requested in 'include' but not found among deferred.",
    )


# Module-level singleton for the ``UploadFile`` default - keeps ruff's
# B008 happy (no function call in argument defaults) and matches FastAPI's
# documented pattern for sharing a default across endpoints.
_UPLOAD_FILE: UploadFile = File(...)


def _resume_value_from(
    request: ResumeRequest,
    current_status: ThreadStatus,
    *,
    paused_payload: dict | None = None,
) -> object:
    """Translate the API resume body to the LangGraph resume value.

    Three shapes:

    - PO checkpoint, **deep mode** (Analyst found ≤ 1 implied story):
      LangGraph expects ``dict[question, answer]`` - pass ``request.answers``
      through.
    - PO checkpoint, **split mode**: LangGraph expects
      ``{"selected_story_titles": [...], "answers": {...}}``. Empty
      ``selected_story_titles`` is the Q2 default - graph treats it as
      "keep every implied story".
    - Story Review checkpoint: ``{"accepted": bool, ...}``.

    ``paused_payload`` carries the current paused state's payload so we
    can discriminate split vs deep at the PO checkpoint without
    inferring from the body shape.
    """
    if current_status == ThreadStatus.PAUSED_PO:
        mode = (paused_payload or {}).get("mode", "deep")
        if mode == "split":
            # Split: structured selection. None / empty list signals
            # "fan out everything" - pass-through, graph fills the default.
            # Issue #207 - prefer the editable ``selected_stories`` shape
            # (title+summary+original_title) when supplied so PO edits to
            # titles flow into ``split_stories``; legacy callers may
            # still send plain ``selected_story_titles``.
            resume: dict[str, object] = {"answers": dict(request.answers or {})}
            if request.selected_stories is not None:
                resume["selected_stories"] = [s.model_dump() for s in request.selected_stories]
            else:
                resume["selected_story_titles"] = list(request.selected_story_titles or [])
            return resume
        if request.answers is None:
            raise HTTPException(
                status_code=400,
                detail="Resume body for paused_po (deep mode) must include 'answers'.",
            )
        return request.answers
    if current_status == ThreadStatus.PAUSED_REVIEW:
        # accepted defaults to True (matches the demo's empty-input behaviour).
        accepted = True if request.accepted is None else bool(request.accepted)
        if accepted:
            return {"accepted": True}
        return {
            "accepted": False,
            "description": request.description or "",
            "objective": request.objective or "",
        }
    raise HTTPException(
        status_code=409,
        detail=f"Thread is not paused (status={current_status.value}); cannot resume.",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: configure logging, assert env safety, mint token, build runner."""
    configure_logging()
    try:
        assert_safe_for_env()
    except ProductionTracingError as exc:
        raise RuntimeError(str(exc)) from exc

    token = generate_token()
    app.state.token_store = TokenStore(token)
    app.state.runner = PipelineRunner()
    _log.info("rtia.api.startup", extra={"event": "startup"})
    yield
    _log.info("rtia.api.shutdown", extra={"event": "shutdown"})


def create_app(runner: PipelineRunner | None = None, token: str | None = None) -> FastAPI:
    """Build a FastAPI app. ``runner`` + ``token`` overrides exist for tests.

    The lifespan still runs for the production case (``runner=None``);
    when a runner is passed in we wire it up directly and skip the
    lifespan's pipeline construction (tests don't want a SQLite saver
    materialised to ``~/.rtia/state.db``).
    """
    if runner is not None:
        app = FastAPI(title="RTIA API")
        app.state.token_store = TokenStore(token or generate_token())
        app.state.runner = runner
    else:
        app = FastAPI(title="RTIA API", lifespan=_lifespan)

    _register_routes(app)
    _install_ui_auth_middleware(app)
    _mount_gradio(app)
    _mount_react(app)
    return app


def _install_ui_auth_middleware(app: FastAPI) -> None:
    """Gate the Gradio mount on the same token as the API routes.

    ``mount_gradio_app`` mounts the UI as a sub-application that bypasses
    FastAPI's ``Depends`` machinery, so per-route auth doesn't apply. The
    middleware accepts the token from any of three sources, in order:

    1. ``Authorization: Bearer …`` header (canonical; what curl uses).
    2. ``?token=…`` query param (one-click open from the printed URL).
    3. ``rtia_token`` cookie (auto-set after a successful #1 or #2,
       so the browser doesn't 401 itself on the absolute-path asset
       fetches Gradio emits - ``/assets/index-*.js`` etc. - which the
       browser fires *without* the original query string).

    API routes ``/pipeline*`` / ``/uploads*`` skip the middleware (they
    have their own ``Depends(verify_token)`` that emits a richer 401),
    but they ALSO accept the cookie because the dependency in
    ``api/auth.py`` reads it. This means an in-browser fetch from the
    Gradio JS bundle to the API hits succeed without the JS needing to
    know the token.

    Cookie is ``HttpOnly``, ``SameSite=Strict``, ``Path=/`` - adequate
    for a localhost-only dev tool. Set ``RTIA_API_COOKIE_SECURE=true``
    if you ever front this with TLS (post-v1 hardening).
    """

    api_prefixes = ("/pipeline", "/uploads", "/docs", "/openapi.json", "/redoc")
    cookie_name = "rtia_token"
    cookie_secure = (os.environ.get("RTIA_API_COOKIE_SECURE") or "").lower() in {
        "true",
        "1",
        "yes",
        "on",
    }

    class _UiAuth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if any(path.startswith(p) for p in api_prefixes):
                return await call_next(request)

            store: TokenStore = request.app.state.token_store
            candidate, source = _extract_token(request, cookie_name)
            if not store.verify(candidate):
                return Response(
                    "Missing or invalid API token.\n",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            response = await call_next(request)
            # Set the cookie when the token arrived via header or query
            # so the browser's subsequent asset fetches authenticate
            # automatically. Skip when the cookie is already the source.
            if source != "cookie":
                response.set_cookie(
                    cookie_name,
                    candidate,
                    httponly=True,
                    samesite="strict",
                    secure=cookie_secure,
                    path="/",
                )
            return response

    app.add_middleware(_UiAuth)


def _extract_token(request: Request, cookie_name: str) -> tuple[str | None, str]:
    """Read the token from header / query / cookie. Returns ``(value, source)``."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip(), "header"
    qp = request.query_params.get("token")
    if qp:
        return qp, "query"
    cookie = request.cookies.get(cookie_name)
    if cookie:
        return cookie, "cookie"
    return None, "none"


def _register_routes(app: FastAPI) -> None:
    @app.post("/pipeline", response_model=ThreadState, dependencies=[Depends(verify_token)])
    def start_pipeline(req: PipelineRequest, request: Request) -> ThreadState:
        runner: PipelineRunner = request.app.state.runner
        try:
            return runner.start(req.requirement_text)
        except InvalidUpdateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/pipeline/{thread_id}", response_model=ThreadState, dependencies=[Depends(verify_token)]
    )
    def get_state(thread_id: str, request: Request) -> ThreadState:
        runner: PipelineRunner = request.app.state.runner
        return runner.get_state(thread_id)

    @app.post(
        "/pipeline/{thread_id}/resume",
        response_model=ThreadState,
        dependencies=[Depends(verify_token)],
    )
    def resume(thread_id: str, body: ResumeRequest, request: Request) -> ThreadState:
        runner: PipelineRunner = request.app.state.runner
        current = runner.get_state(thread_id)
        resume_value = _resume_value_from(body, current.status, paused_payload=current.payload)
        try:
            return runner.resume(thread_id, resume_value)
        except InvalidUpdateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/pipeline/{thread_id}/export",
        response_model=ExportResult,
        dependencies=[Depends(verify_token)],
    )
    def export_to_backlog(thread_id: str, body: ExportRequest, request: Request) -> ExportResult:
        """Push the thread's final artifact to Jira or GitHub.

        ``body.dry_run=true`` short-circuits the HTTP call and returns
        the would-be payload - safe to call without credentials.
        """
        runner: PipelineRunner = request.app.state.runner
        loaded = runner.get_artifact_and_title(thread_id)
        if loaded is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No final artifact for this thread yet. "
                    "The pipeline hasn't reached the Composer."
                ),
            )
        artifact, title = loaded
        rendered = artifact.as_markdown()

        try:
            exporter = make_exporter(body.target.backend)
            # Issue #208 - when ``update_issue_id`` is supplied, replace
            # the existing issue instead of creating a duplicate. The
            # common flow: PO copies a split placeholder's issue number,
            # re-runs RTIA on the title, sets ``update_issue_id`` to
            # collapse the deep artifact onto the placeholder.
            if body.update_issue_id:
                return exporter.update_issue(
                    body.update_issue_id,
                    rendered,
                    body.target,
                    title=title,
                    dry_run=body.dry_run,
                )
            return exporter.export(rendered, body.target, title=title, dry_run=body.dry_run)
        except ExportConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ExporterTransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post(
        "/pipeline/{thread_id}/export-deferred",
        response_model=DeferredExportResponse,
        dependencies=[Depends(verify_token)],
    )
    def export_deferred(
        thread_id: str, body: DeferredExportRequest, request: Request
    ) -> DeferredExportResponse:
        """Create one follow-up issue per deferred implied story.

        Phase 15.3. When the PO picked one of several implied stories at
        the PO checkpoint, the others were intentionally left out of the
        finished artifact. This endpoint pushes a lightweight placeholder
        issue for each so they land on the backlog instead of being lost.
        """
        runner: PipelineRunner = request.app.state.runner
        # Phase 15.4 - dispatch to the split source on DONE_SPLIT threads.
        # Split stories are the same shape (ImpliedStory) as deferred, so
        # the rest of the loop body is identical. The shared helper picks
        # which state field to read; the UI's "Create follow-up issues"
        # button uses the same call.
        current = runner.get_state(thread_id)
        loaded = select_followup_source(runner, thread_id, current.status)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Thread not found or has no state.")
        deferred, requirement_text = loaded
        if not deferred:
            return DeferredExportResponse(results=[], skipped=[])

        # Apply optional include-filter case-insensitively.
        include_lower: set[str] | None = None
        if body.include is not None:
            include_lower = {t.strip().lower() for t in body.include if t and t.strip()}

        try:
            exporter = make_exporter(body.target.backend)
        except ExportConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        results: list[ExportResult] = []
        seen_titles: set[str] = set()
        for story in deferred:
            seen_titles.add(story.title.lower())
            if include_lower is not None and story.title.lower() not in include_lower:
                continue
            md = build_followup_markdown(
                story.title,
                story.summary,
                requirement_excerpt=requirement_text,
            )
            try:
                result = exporter.export(md, body.target, title=story.title, dry_run=body.dry_run)
            except ExportConfigError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except ExporterTransportError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            results.append(result)

        skipped: list[str] = []
        if include_lower is not None:
            skipped = sorted(include_lower - seen_titles)

        return DeferredExportResponse(results=results, skipped=skipped)

    @app.get(
        "/pipeline/{thread_id}/export.md",
        response_class=PlainTextResponse,
        dependencies=[Depends(verify_token)],
    )
    def export_markdown(thread_id: str, request: Request) -> PlainTextResponse:
        runner: PipelineRunner = request.app.state.runner
        md = runner.render_markdown(thread_id)
        if md is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No final artifact for this thread yet. "
                    "The pipeline hasn't reached the Composer."
                ),
            )
        return PlainTextResponse(
            md,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="rtia-{thread_id}.md"',
            },
        )

    @app.post("/uploads/pdf", response_model=UploadResult, dependencies=[Depends(verify_token)])
    async def upload_pdf(file: UploadFile = _UPLOAD_FILE) -> UploadResult:
        data = await file.read()
        try:
            text = extract_pdf(data)
        except ScannedPdfError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "scanned_pdf", "message": str(exc)},
            ) from exc
        except FileTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return UploadResult(text=text, char_count=len(text))

    @app.post(
        "/uploads/markdown",
        response_model=UploadResult,
        dependencies=[Depends(verify_token)],
    )
    async def upload_markdown(file: UploadFile = _UPLOAD_FILE) -> UploadResult:
        data = await file.read()
        try:
            text = extract_markdown(data)
        except ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return UploadResult(text=text, char_count=len(text))


def _mount_gradio(app: FastAPI) -> None:
    """Mount the Gradio Blocks app at ``/legacy``.

    Imported lazily so importing ``api.main`` for unit tests doesn't pay
    the Gradio import cost (it pulls in numpy, pillow, etc.). Tests that
    don't care about the UI mount construct ``create_app`` and use
    ``TestClient`` against the API routes only.

    Epic 6 (#291) moved this from ``/`` to ``/legacy`` so the React SPA
    can take over the root. The mount is removed entirely in US-26.
    """
    import gradio as gr

    from ui.gradio_app import build_blocks

    # The bare ``/legacy`` path needs an explicit redirect to ``/legacy/``
    # because ``mount_gradio_app`` mounts an inner ASGI app whose routes
    # start at ``/``; Starlette forwards the exact-match ``/legacy`` to
    # the inner app with an empty path, which it can't serve. Register
    # the redirect **before** the mount so the route table sees it first.
    @app.get("/legacy", include_in_schema=False)
    def _legacy_redirect() -> RedirectResponse:
        return RedirectResponse(url="/legacy/", status_code=307)

    blocks = build_blocks(app)
    gr.mount_gradio_app(app, blocks, path="/legacy")


_REACT_DIST = Path(__file__).resolve().parent.parent / "ui-react" / "dist"
_REACT_BUILD_HINT = (
    "ui-react build directory not found.\n"
    "Run: cd ui-react && npm install && npm run build\n"
    f"Expected: {_REACT_DIST}\n"
)


def _mount_react(app: FastAPI) -> None:
    """Serve the React SPA at ``/`` from ``ui-react/dist/``.

    When the build directory is missing (clean checkout that hasn't run
    ``npm run build`` yet), register a fallback route that returns a
    500 with build instructions instead of letting Starlette surface a
    bare ``RuntimeError: Directory does not exist`` traceback.

    Must be called *after* ``_mount_gradio`` so the ``/legacy`` Gradio
    sub-app isn't shadowed by the ``/`` static mount.
    """
    if not _REACT_DIST.is_dir() or not (_REACT_DIST / "index.html").is_file():

        @app.get("/", include_in_schema=False)
        def _react_build_missing() -> PlainTextResponse:
            return PlainTextResponse(_REACT_BUILD_HINT, status_code=500)

        return

    app.mount("/", StaticFiles(directory=_REACT_DIST, html=True), name="react-ui")


__all__ = ["create_app"]
