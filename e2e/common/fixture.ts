// Playwright fixtures (the `test.extend` dependency-injection layer). This is
// where the extended `test` is DEFINED — page objects, step classes, and the
// auth-seeded `page` are all wired here, so specs receive them via DI with zero
// `new`. Specs import from `@common/base` (the stable entry surface), which
// re-exports this; nothing should import this file directly except `base.ts`.
//
// Note the deliberate singular/plural split: `common/fixture.ts` (this file) is
// the Playwright-fixtures DI; `fixtures/` (plural) is static test DATA
// (constants, sample files). Two different meanings of "fixture".
//
// Auth: the SPA's lib/api.ts:getToken() reads ?token=… on first visit, writes
// it to localStorage.rtia_token (sent as a Bearer header on every fetch — no
// cookie), then strips the query param. The `page` override navigates once to
// /?token=<pinned> so specs read as pure business flows. baseURL comes from the
// active project (one fake-backend port per scenario).
import { test as base, expect } from '@playwright/test';

import { PINNED_TOKEN } from '@fixtures/constants';

import IntakePage from '@pages/intake.page';
import RunPage from '@pages/run.page';
import PoDeepCheckpointPage from '@pages/po-deep-checkpoint.page';
import PoSplitCheckpointPage from '@pages/po-split-checkpoint.page';
import StoryReviewPage from '@pages/story-review.page';
import ResultPage from '@pages/result.page';
import ExportPage from '@pages/export.page';
import ErrorPage from '@pages/error.page';

import IntakeSteps from '@steps/intake.steps';
import RunSteps from '@steps/run.steps';
import PoDeepSteps from '@steps/po-deep.steps';
import PoSplitSteps from '@steps/po-split.steps';
import StoryReviewSteps from '@steps/story-review.steps';
import ResultSteps from '@steps/result.steps';
import ExportSteps from '@steps/export.steps';
import ErrorSteps from '@steps/error.steps';

interface Pages {
  intakePage: IntakePage;
  runPage: RunPage;
  poDeepPage: PoDeepCheckpointPage;
  poSplitPage: PoSplitCheckpointPage;
  storyReviewPage: StoryReviewPage;
  resultPage: ResultPage;
  exportPage: ExportPage;
  errorPage: ErrorPage;
}

interface Steps {
  intakeSteps: IntakeSteps;
  runSteps: RunSteps;
  poDeepSteps: PoDeepSteps;
  poSplitSteps: PoSplitSteps;
  storyReviewSteps: StoryReviewSteps;
  resultSteps: ResultSteps;
  exportSteps: ExportSteps;
  errorSteps: ErrorSteps;
}

const test = base.extend<Pages & Steps>({
  // Auth seed: first navigation carries the token; getToken() persists it to
  // localStorage and strips it from the visible URL.
  page: async ({ page }, use) => {
    await page.goto(`/?token=${PINNED_TOKEN}`);
    await use(page);
  },

  // Page objects — locators + atomic interactions, no assertions.
  intakePage: async ({ page }, use) => {
    await use(new IntakePage(page));
  },
  runPage: async ({ page }, use) => {
    await use(new RunPage(page));
  },
  poDeepPage: async ({ page }, use) => {
    await use(new PoDeepCheckpointPage(page));
  },
  poSplitPage: async ({ page }, use) => {
    await use(new PoSplitCheckpointPage(page));
  },
  storyReviewPage: async ({ page }, use) => {
    await use(new StoryReviewPage(page));
  },
  resultPage: async ({ page }, use) => {
    await use(new ResultPage(page));
  },
  exportPage: async ({ page }, use) => {
    await use(new ExportPage(page));
  },
  errorPage: async ({ page }, use) => {
    await use(new ErrorPage(page));
  },

  // Step classes — workflows + all assertions, composed from page objects.
  intakeSteps: async ({ intakePage, runPage }, use) => {
    await use(new IntakeSteps(intakePage, runPage));
  },
  runSteps: async ({ runPage, intakePage, poDeepPage, errorPage }, use) => {
    await use(new RunSteps(runPage, intakePage, poDeepPage, errorPage));
  },
  poDeepSteps: async ({ poDeepPage }, use) => {
    await use(new PoDeepSteps(poDeepPage));
  },
  poSplitSteps: async ({ poSplitPage, poDeepPage }, use) => {
    await use(new PoSplitSteps(poSplitPage, poDeepPage));
  },
  storyReviewSteps: async ({ storyReviewPage }, use) => {
    await use(new StoryReviewSteps(storyReviewPage));
  },
  resultSteps: async ({ resultPage }, use) => {
    await use(new ResultSteps(resultPage));
  },
  exportSteps: async ({ exportPage }, use) => {
    await use(new ExportSteps(exportPage));
  },
  errorSteps: async ({ errorPage, exportPage, resultPage }, use) => {
    await use(new ErrorSteps(errorPage, exportPage, resultPage));
  },
});

export default test;
export { expect };
