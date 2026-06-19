// Trace-readability wrapper for step-class methods. No Allure: RTIA has no
// allure dependency and none is wanted, so `step()` is a thin pass-through over
// Playwright's native `test.step`, which groups actions/assertions under a
// labelled node in the trace + HTML report. Step classes call this instead of
// carrying a decorator, which keeps the existing strict/ESM tsconfig untouched
// (no experimentalDecorators-vs-standard-decorator mismatch to ship).
import { test } from '@playwright/test';

export function step<T>(label: string, body: () => Promise<T>): Promise<T> {
  return test.step(label, body);
}
