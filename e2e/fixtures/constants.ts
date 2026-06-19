// Shared constants between the Playwright config, the auth fixture, and the
// start-backends.sh helper. The token is PINNED (not minted per process) so
// the fixture can seed it via ?token= without coordinating with a running
// backend's stdout. start-backends.sh exports the same value as
// RTIA_API_TOKEN; api/auth.py:generate_token() returns the explicit env value
// when set, so the two stay in lockstep.
export const PINNED_TOKEN = 'e2e-pinned-token';

// One fake scenario == one backend process == one port == one Playwright
// project. RTIA_FAKE_SCENARIO is process-wide (re-read per invoke), so the
// scenarios cannot share a single backend.
export const BACKENDS = {
  deep_clean: { port: 8001, baseURL: 'http://127.0.0.1:8001' },
  deep_with_po: { port: 8002, baseURL: 'http://127.0.0.1:8002' },
  split: { port: 8003, baseURL: 'http://127.0.0.1:8003' },
  error: { port: 8004, baseURL: 'http://127.0.0.1:8004' },
} as const;
