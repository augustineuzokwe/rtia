import { expect } from '@playwright/test';
import { step } from '@common/step';
import type ExportPage from '@pages/export.page';

// Export-panel workflows + assertions for both terminal artifact states.
export default class ExportSteps {
  constructor(private readonly exportPanel: ExportPage) {}

  async verifyDeepExportPanel(): Promise<void> {
    await step('Verify the export panel is in deep mode with the backlog-push controls', async () => {
      await expect(this.exportPanel.panel).toBeVisible();
      await expect(this.exportPanel.panel).toHaveAttribute('data-mode', 'deep');
      await expect(this.exportPanel.submit).toHaveText('Push to backlog');
      await expect(this.exportPanel.dryRun).toBeChecked();
    });
  }

  async verifyDeepMode(): Promise<void> {
    await step('Verify the export panel is in deep mode', async () => {
      await expect(this.exportPanel.panel).toHaveAttribute('data-mode', 'deep');
    });
  }

  async verifySplitExportPanel(): Promise<void> {
    await step('Verify the export panel switched to split mode with the follow-up controls', async () => {
      await expect(this.exportPanel.panel).toBeVisible();
      await expect(this.exportPanel.panel).toHaveAttribute('data-mode', 'split');
      await expect(this.exportPanel.submit).toHaveText('Create follow-up issues');
    });
  }

  async verifyDryRunDefault(): Promise<void> {
    await step('Verify dry-run is the default so no live issue is created', async () => {
      await expect(this.exportPanel.dryRun).toBeChecked();
    });
  }

  async runGithubDryRunExport(repo: string): Promise<void> {
    await step(`Run a GitHub dry-run export against "${repo}"`, async () => {
      await this.exportPanel.setGithubRepo(repo);
      await this.exportPanel.submitExport();
    });
  }

  async verifyDeferredResults(keptCount: number, expectedTitle: string): Promise<void> {
    await step('Verify one export result per kept story and the renamed title flowed through', async () => {
      await expect(this.exportPanel.results).toBeVisible();
      for (let i = 0; i < keptCount; i += 1) {
        await expect(this.exportPanel.result(i)).toBeVisible();
      }
      // The dropped story is excluded, so the next index never renders.
      await expect(this.exportPanel.result(keptCount)).not.toBeAttached();
      await expect(this.exportPanel.results).toContainText(expectedTitle);
    });
  }
}
