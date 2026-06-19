# RTIA E2E (Playwright)

End-to-end tests for the RTIA React SPA, driven against four **deterministic
fake-LLM backends** (no Gemini calls, zero cost, no flakiness from model
nondeterminism). This is the `e2e` package of the repo-root pnpm workspace.

> **Stage 1 (scaffold) only.** `tests/` is intentionally empty. Specs land in
> Stage 2.

## Why four backends

`RTIA_FAKE_SCENARIO` is **process-wide** (re-read per invoke), so one running
backend can only ever serve a single scenario. Each scenario therefore gets its
own backend process on its own port, and each maps to one Playwright project:

| Project        | baseURL                  | Backend scenario              |
| -------------- | ------------------------ | ----------------------------- |
| `deep-clean`   | http://127.0.0.1:8001    | `deep_clean`                  |
| `deep-with-po` | http://127.0.0.1:8002    | `deep_with_po`                |
| `split`        | http://127.0.0.1:8003    | `split`                       |
| `error`        | http://127.0.0.1:8004    | `error`                       |
| `upload`       | http://127.0.0.1:8001    | reuses the `deep_clean` backend |

## Prerequisites

- Node 20.15.1, pnpm 9.7.1 (repo-root workspace)
- `uv` + a synced Python venv (`uv sync` at repo root) for the backends
- Chromium installed for Playwright (see below)

## One-time setup

```bash
# from repo root
pnpm install                       # installs the e2e package too
pnpm --filter e2e install-browsers # playwright install --with-deps chromium
```

## Running the suite

The backends are started **externally** — Playwright does not spawn them (one
`webServer` can't fan out to four scenario processes). `globalSetup` builds the
SPA once (if `ui-react/dist/` is missing) and health-checks the four ports,
failing fast with a pointer back here if any backend is down.

```bash
# 1. start the four fake backends (8001–8004), pinned token
pnpm --filter e2e backends:start      # or: bash e2e/scripts/start-backends.sh

# 2. run the tests
pnpm --filter e2e test                # or: --filter e2e test:headed
#    a single scenario:
pnpm --filter e2e exec playwright test --project deep-clean

# 3. stop the backends when done
pnpm --filter e2e backends:stop       # or: bash e2e/scripts/stop-backends.sh
```

Backend logs land in `e2e/scripts/.logs/<scenario>.log`; PIDs in
`e2e/scripts/.pids/` (both gitignored).

## Auth model

The SPA reads `?token=…` on first load (`ui-react/src/lib/api.ts:getToken()`),
persists it to `localStorage.rtia_token`, sends it as a **Bearer header** (no
cookie), and strips the query param from the URL. The token is **pinned** to
`e2e-pinned-token` in two places that must stay in sync:

- `e2e/fixtures/constants.ts` → `PINNED_TOKEN` (the auth fixture seeds `?token=`)
- `e2e/scripts/start-backends.sh` → `RTIA_API_TOKEN` (each backend honours it
  via `api/auth.py:generate_token()`)

The auth fixture (`e2e/fixtures/auth.ts`) overrides `page` so each test's first
navigation lands on `/?token=<pinned>`, exactly mirroring a human's one-click URL.
