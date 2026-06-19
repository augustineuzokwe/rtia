import { expect } from '@playwright/test';
import { step } from '@common/step';
import type ErrorPage from '@pages/error.page';
import type ExportPage from '@pages/export.page';
import type ResultPage from '@pages/result.page';

// Error-panel workflows + assertions for the terminal error state, including
// the details disclosure toggle and start-over.
export default class ErrorSteps {
  constructor(
    private readonly error: ErrorPage,
    private readonly exportPanel: ExportPage,
    private readonly result: ResultPage,
  ) {}

  async verifyErrorPanel(agent: string, exceptionClass: string): Promise<void> {
    await step('Verify the error panel, message, and meta block', async () => {
      await expect(this.error.panel).toBeVisible();
      await expect(this.error.message).toBeVisible();
      await expect(this.error.message).not.toBeEmpty();
      await expect(this.error.meta).toBeVisible();
      await expect(this.error.agent).toContainText(agent);
      await expect(this.error.exceptionClass).toContainText(exceptionClass);
    });
  }

  async verifyNoArtifactPanels(): Promise<void> {
    await step('Verify no export or result panel lingers in the error state', async () => {
      await expect(this.exportPanel.panel).not.toBeAttached();
      await expect(this.result.panel).not.toBeAttached();
    });
  }

  async revealDetails(): Promise<void> {
    await step('Reveal the error details disclosure', async () => {
      await this.error.clickToggleDetails();
      await expect(this.error.toggleDetails).toHaveText('Hide details');
      await expect(this.error.details).toBeVisible();
      await expect(this.error.raw).toBeVisible();
      await expect(this.error.stub).toBeVisible();
    });
  }

  async hideDetails(): Promise<void> {
    await step('Hide the error details disclosure', async () => {
      await this.error.clickToggleDetails();
      await expect(this.error.details).not.toBeAttached();
      await expect(this.error.toggleDetails).toHaveText('Show details');
    });
  }

  async startOver(): Promise<void> {
    await step('Click start over from the error panel', async () => {
      await this.error.clickStartOver();
    });
  }
}
