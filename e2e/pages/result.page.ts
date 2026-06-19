import type { Locator, Page } from '@playwright/test';

// The result panel shown on the deep done state: the composed artifact
// preview, plus the empty-state marker that must NOT be present once a result
// exists.
export default class ResultPage {
  readonly panel: Locator;
  readonly preview: Locator;
  readonly empty: Locator;

  constructor(page: Page) {
    this.panel = page.getByTestId('result-panel');
    this.preview = page.getByTestId('result-preview');
    this.empty = page.getByTestId('result-empty');
  }
}
