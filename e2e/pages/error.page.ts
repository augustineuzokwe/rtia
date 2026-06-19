import type { Locator, Page } from '@playwright/test';

// The error panel shown on the terminal error state: the message, the meta
// block (failing agent + exception class), and a details disclosure that
// toggles the raw payload + stub. Has its own start-over control.
export default class ErrorPage {
  readonly panel: Locator;
  readonly message: Locator;
  readonly meta: Locator;
  readonly agent: Locator;
  readonly exceptionClass: Locator;
  readonly toggleDetails: Locator;
  readonly details: Locator;
  readonly raw: Locator;
  readonly stub: Locator;
  readonly startOver: Locator;

  constructor(page: Page) {
    this.panel = page.getByTestId('error-panel');
    this.message = page.getByTestId('error-message');
    this.meta = page.getByTestId('error-meta');
    this.agent = page.getByTestId('error-agent');
    this.exceptionClass = page.getByTestId('error-class');
    this.toggleDetails = page.getByTestId('error-toggle-details');
    this.details = page.getByTestId('error-details');
    this.raw = page.getByTestId('error-raw');
    this.stub = page.getByTestId('error-stub');
    this.startOver = page.getByTestId('error-start-over');
  }

  async clickToggleDetails(): Promise<void> {
    await this.toggleDetails.click();
  }

  async clickStartOver(): Promise<void> {
    await this.startOver.click();
  }
}
