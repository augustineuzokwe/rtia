# ADR-0012: v1 = single-user local clone-and-run, operator-supplied keys

**Status:** Accepted (2026-05-26)
**Author:** augustineuzokwe
**Decision driver:** Phase 16 of the road-to-production plan is closing; before drafting the public README + blog launch, the project needs an explicit, citeable definition of what "v1" includes and - critically - what it deliberately does not. Without this ADR, post-v1 scope creep happens silently.

## Context

RTIA reached functional parity with its Phase 1–15 roadmap on `main` at commit `6dc8a83` (post-PR #232 glossary). Before the v1 launch (README polish + public blog + LinkedIn announce), the team needs one place that names the boundary: which adopter profile we support, which we don't, and the cross-references to the in-codebase decisions that follow from that line.

Two real adopter profiles competed for "v1":

1. **Single-user local clone-and-run.** A developer clones the repo, runs `uv sync`, drops their own Gemini key into `.env`, runs the CLI demo or `scripts/run_api.py`, gets backlog-ready user stories. No external deployment, no multi-tenant, no auth beyond the per-process bearer token RTIA already mints.

2. **Hosted multi-user service.** Operators deploy RTIA somewhere durable, end-users hit it via a UI or API, the operator pays the LLM bill.

Profile (2) is the path to commercial adoption but adds an enormous surface area: container packaging, persistent multi-user storage, OAuth or session auth, rate limiting per user, TLS termination, secret rotation, billing attribution, and observability across tenants. None of that is built today and none of it is on Phase 1–16 of the plan. Pretending v1 covers profile (2) would either delay launch by months or ship something dangerous.

## Decision

**v1 = single-user local clone-and-run, operator-supplied keys.**

The supported workflow:

1. Adopter clones the repo, runs `uv sync`, drops their own credentials (`GOOGLE_API_KEY` at minimum; optionally `GITHUB_TOKEN` / `JIRA_*` / `LANGSMITH_API_KEY`) into `.env`.
2. Adopter runs either `uv run python scripts/run_pipeline_demo.py …` (CLI), `uv run python scripts/run_api.py` (FastAPI + Gradio UI on `127.0.0.1:8000` with a one-process bearer token), or invokes the package as a library.
3. State persists at `~/.rtia/state.db` (SQLite, single-writer; see [ADR-0002](adr-0002-durable-checkpointer.md)).
4. Costs accrue to the adopter's own LLM provider account. RTIA does not proxy, gateway, or attribute spend.

That's v1. Everything else is out of scope.

## Out of scope for v1 (explicit non-goals)

These are intentional omissions, not gaps. Promoting any of them into scope requires an amending ADR.

- **Docker / container packaging.** `uv sync` is the supported install. Containerisation can come later if a hosted deployment lands.
- **Multi-tenant data isolation.** SQLite is a single-writer store; the API mints one bearer token per process. There is no per-user partitioning of threads, no per-user usage caps, no per-user audit trail. See [ADR-0002 §"Why SQLite, not Postgres"](adr-0002-durable-checkpointer.md) for the swap path when multi-user lands.
- **OAuth, SSO, or any auth beyond the per-process bearer token.** The token gates `/pipeline*` and `/uploads/*` on `127.0.0.1`; that is sufficient for a single operator on their own machine, and unfit for anything else.
- **Per-user rate limiting + quota enforcement.** The runtime token-budget gate in `pyproject.toml [tool.rtia.budgets]` is process-wide, not per-user.
- **TLS termination.** API listens on `127.0.0.1:8000` by default. There is no out-of-the-box HTTPS, no reverse-proxy config, no certificate management.
- **Promptfoo regression suite.** RTIA's own eval gate (`evals/run_evals.py` + `.github/workflows/ci.yml`) covers the prompt-regression need at v1's scale. See plan §6.5 for the explicit "when to add Promptfoo" trigger.
- **Cross-sample concurrency in the eval suite.** Documented as deliberate non-goal in [`docs/pipeline-baseline-2026-05-24.md` §"Out of scope for the speed-up PR"](pipeline-baseline-2026-05-24.md). The current per-sample telemetry capture is process-global and would cross-bleed under concurrency.
- **Load / concurrency tests.** v1 is explicitly single-user; load testing the SQLite saver under simulated multi-process write contention would measure the wrong thing.
- **Hosted / SaaS deployment of any kind.** No deployment docs, no terraform, no kubernetes manifests, no managed-database instructions.

Out of scope **but already in flight** as separate Tasks:

- LLM response cache with prompt-hash key and short TTL - tracked at [Issue #230](https://github.com/augustineuzokwe/rtia/issues/230). Implementation-ready; lands as its own PR.
- Stochastic AC validation (`--n-runs` flag, pass-rate thresholds) - tracked at [Issue #233](https://github.com/augustineuzokwe/rtia/issues/233), depends on #230 landing first.

Out of scope **and not on any current Task** (would need a new issue first):

- `rtia doctor` end-to-end install verifier (per plan §6.5 - "polish, not load-bearing").
- Provider-abstraction factory `build_chat_llm()` - only justified by a third LLM provider per [ADR-0006](adr-0006-provider-switch.md).
- `scripts/threads.py` CLI for `list / show / clear / prune` over `~/.rtia/state.db` (per plan §2 followup task).

## Consequences

### Positive

- **Focused scope ships sooner.** v1 launch (README polish + blog + LinkedIn announce) is bounded by the existing code; no new infra to build, no new auth flow to design.
- **Operator-supplied keys remove RTIA from the cost-attribution path.** No billing, no quotas, no abuse handling required.
- **Single-writer SQLite is appropriate for the workload.** A single human operator hitting the API or CLI cannot generate write contention; the durable checkpointer's job (survive process restart so a paused PO can resume hours later) holds intact.
- **Security surface is small.** No public listener, no shared state, no untrusted users.
- **Test architecture is sized to scope.** 463 tests + a $0.03 eval gate + adversarial samples 04–07 cover what a single-user installation actually does. Load tests, multi-tenant isolation tests, and adversarial-tenant scenarios are correctly absent.

### Trade-offs

- **Cannot deploy multi-user without an amending ADR.** Any team that wants to host RTIA for multiple end-users has work to do (Postgres swap per ADR-0002, OAuth, per-user quotas, TLS, audit trail). The README's "what RTIA does" section will be explicit about this.
- **No deployment story for non-technical adopters.** The supported adopter is comfortable with `uv sync` and editing `.env`. Anyone without that comfort needs the README to point them at the API/UI run pattern, but they will not get a one-click installer.
- **The `RTIA_LLM_PROVIDER=ollama` path stays opt-in only.** Per [`docs/ollama-probe-2026-05-26.md`](ollama-probe-2026-05-26.md), local-model quality regresses > 15 % on three Analyst-side metrics; Gemini Flash stays the v1 default. Adopters wanting a zero-API path can flip the switch but should expect lower fidelity.

### How this lands in user-facing docs

- README v1 calls out the supported workflow (clone → `uv sync` → `.env` → run) and links here for the "what's not supported" detail.
- Blog Section 9 ("Try it yourself") follows the same recipe.
- Any future contributor PR that touches scope (adds an auth flow, attempts containerisation, etc.) should be challenged against this ADR - either it's an amendment to the ADR, or it's out of scope.

## References - the in-codebase decisions this ADR depends on

- [ADR-0002 - durable checkpointer](adr-0002-durable-checkpointer.md) - SQLite for v1; Postgres path preserved at the `build_pipeline(checkpointer=…)` seam.
- [ADR-0006 - provider switch (Claude → Gemini)](adr-0006-provider-switch.md) - one provider, one consumer per import site; no abstraction factory until a 3rd provider lands.
- [ADR-0007 - Gemini 3.5 Flash switch](adr-0007-gemini-3-5-flash-switch.md) - the model the v1 default is pinned to; live-probing methodology that backs the choice.
- [ADR-0008 - PII vs LangSmith](adr-0008-pii-langsmith.md) - production-tracing guard (`RTIA_ENV=production` + `LANGSMITH_TRACING=true` refuses to start); informs the "single-user with operator-supplied keys" decision because the operator owns the trace-storage policy.
- [ADR-0010 - multi-story fan-out](adr-0010-multi-story-fan-out.md) - the conditional LangGraph edge that bumped `PIPELINE_STATE_VERSION` 1→2; v1 ships with version 2.

## References - supporting artifacts

- [pipeline-baseline-2026-05-26.md](pipeline-baseline-2026-05-26.md) - the calibrated quality + cost numbers the v1 README will cite.
- [ollama-probe-2026-05-26.md](ollama-probe-2026-05-26.md) - the local-model fallback evaluation that justifies "Gemini stays default" in v1.
- Plan at `/Users/auzokwe/.claude/plans/before-we-draft-adr-declarative-leaf.md` - the working document this ADR codifies.
