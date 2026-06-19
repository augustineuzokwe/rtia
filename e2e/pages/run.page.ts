import type { Locator, Page } from '@playwright/test';

// The run view chrome that frames every post-intake status: the run panel,
// the summary header, the status badge (carries data-status), the poll
// indicator / poll-stopped pair, and the start-over control. Panel-specific
// regions (PO checkpoints, review, result, export, error) are their own page
// objects; this one owns only the shared run-view scaffolding.
export default class RunPage {
  readonly panel: Locator;
  readonly summary: Locator;
  readonly statusBadge: Locator;
  readonly pollIndicator: Locator;
  readonly pollStopped: Locator;
  readonly startOver: Locator;

  constructor(private readonly page: Page) {
    this.panel = page.getByTestId('run-panel');
    this.summary = page.getByTestId('run-summary');
    this.statusBadge = page.getByTestId('status-badge');
    this.pollIndicator = page.getByTestId('poll-indicator');
    this.pollStopped = page.getByTestId('poll-stopped');
    this.startOver = page.getByTestId('start-over');
  }

  async clickStartOver(): Promise<void> {
    await this.startOver.click();
  }
}
