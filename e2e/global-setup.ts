// Runs once before any project. Two jobs:
//
//   1. Build the React SPA exactly once. Each backend serves ui-react/dist at
//      /, so the bundle must exist before the first navigation. The build is
//      here (not in a per-project webServer.command) because four projects
//      would otherwise race on writing dist/.
//   2. Fail fast if the four fake backends aren't reachable. Playwright does
//      NOT spawn them (one webServer can't fan out to four scenario
//      processes) — they're started externally by scripts/start-backends.sh.
//      A clear early failure beats four projects each timing out on goto().

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { FullConfig } from '@playwright/test';
import { BACKENDS } from './data/constants';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..');
const DIST_INDEX = resolve(REPO_ROOT, 'ui-react', 'dist', 'index.html');

function buildSpaOnce(): void {
  if (existsSync(DIST_INDEX)) {
    console.log(
      `[global-setup] SPA bundle present, skipping build (delete ui-react/dist/ to force a rebuild): ${DIST_INDEX}`,
    );
    return;
  }
  console.log('[global-setup] Building SPA (pnpm --filter ui-react build)…');
  execFileSync('pnpm', ['--filter', 'ui-react', 'build'], {
    cwd: REPO_ROOT,
    stdio: 'inherit',
  });
}

async function assertBackendsReachable(): Promise<void> {
  const unreachable: string[] = [];
  for (const [scenario, { baseURL }] of Object.entries(BACKENDS)) {
    try {
      // GET / returns the SPA shell (200) once a backend is up. We tolerate
      // any non-network response — only a thrown fetch means "not listening".
      await fetch(baseURL, { method: 'GET' });
    } catch {
      unreachable.push(`  ${scenario.padEnd(13)} ${baseURL}`);
    }
  }
  if (unreachable.length > 0) {
    throw new Error(
      [
        'Fake backends are not reachable:',
        ...unreachable,
        '',
        'Start them first:',
        '  pnpm --filter e2e backends:start',
        '  (or: bash e2e/scripts/start-backends.sh)',
      ].join('\n'),
    );
  }
  console.log('[global-setup] All 4 fake backends reachable.');
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  buildSpaOnce();
  await assertBackendsReachable();
}
