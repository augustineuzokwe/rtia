import { expect } from '@playwright/test';
import { step } from '@common/step';
import type ResultPage from '@pages/result.page';

// Result-panel workflows + assertions for the deep done state.
export default class ResultSteps {
  constructor(private readonly result: ResultPage) {}

  async verifyResultPanel(): Promise<void> {
    await step('Verify the result panel renders with a non-empty preview and no empty-state', async () => {
      await expect(this.result.panel).toBeVisible();
      await expect(this.result.preview).not.toBeEmpty();
      await expect(this.result.empty).not.toBeAttached();
    });
  }

  async verifyResultVisible(): Promise<void> {
    await step('Verify the result panel is visible', async () => {
      await expect(this.result.panel).toBeVisible();
    });
  }

  async verifyResultContains(text: string): Promise<void> {
    await step(`Verify the result preview contains "${text}"`, async () => {
      await expect(this.result.preview).toContainText(text);
    });
  }

  async verifyResultPanelAbsent(): Promise<void> {
    await step('Verify the result panel is not present', async () => {
      await expect(this.result.panel).not.toBeAttached();
    });
  }
}
