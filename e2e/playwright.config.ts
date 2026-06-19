import { defineConfig, devices } from '@playwright/test';
import { BACKENDS } from './fixtures/constants';

// Five projects, Chromium only. Four map 1:1 to the four deterministic fake
// backends (one scenario per process per port — RTIA_FAKE_SCENARIO is
// process-wide). The fifth ("upload") reuses the deep_clean backend on 8001;
// it exists as a separate project so upload specs can be selected/filtered
// independently of the deep_clean flow specs.
//
// Backends are started EXTERNALLY (scripts/start-backends.sh); globalSetup
// builds the SPA once and health-checks the four ports. There is no
// webServer block — one webServer can't fan out to four scenario processes.
export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  // Each project pins testMatch to its own spec file — without it every
  // project would run all five specs against the wrong scenario backend.
  projects: [
    {
      name: 'deep-clean',
      testMatch: '**/deep-clean.spec.ts',
      use: { ...devices['Desktop Chrome'], baseURL: BACKENDS.deep_clean.baseURL },
    },
    {
      name: 'deep-with-po',
      testMatch: '**/deep-with-po.spec.ts',
      use: { ...devices['Desktop Chrome'], baseURL: BACKENDS.deep_with_po.baseURL },
    },
    {
      name: 'split',
      testMatch: '**/split.spec.ts',
      use: { ...devices['Desktop Chrome'], baseURL: BACKENDS.split.baseURL },
    },
    {
      name: 'error',
      testMatch: '**/error.spec.ts',
      use: { ...devices['Desktop Chrome'], baseURL: BACKENDS.error.baseURL },
    },
    {
      // Reuses the deep_clean backend (8001); separate project so upload
      // specs filter cleanly via `--project upload`.
      name: 'upload',
      testMatch: '**/upload.spec.ts',
      use: { ...devices['Desktop Chrome'], baseURL: BACKENDS.deep_clean.baseURL },
    },
  ],
});
