import { expect } from '@playwright/test';
import { step } from '@common/step';
import type RunPage from '@pages/run.page';
import type IntakePage from '@pages/intake.page';
import type PoDeepCheckpointPage from '@pages/po-deep-checkpoint.page';
import type ErrorPage from '@pages/error.page';

// Run-view scaffolding workflows + assertions: that the run view took over
// from intake, status-badge transitions (via data-status), polling state
// (indicator while non-terminal, poll-stopped when terminal), and start-over.
export default class RunSteps {
  constructor(
    private readonly run: RunPage,
    private readonly intake: IntakePage,
    private readonly poDeep: PoDeepCheckpointPage,
    private readonly error: ErrorPage,
  ) {}

  async verifyRunViewTookOver(): Promise<void> {
    await step('Verify the run view replaced the intake view', async () => {
      await expect(this.run.panel).toBeVisible();
      await expect(this.intake.panel).not.toBeAttached();
      await expect(this.run.summary).toBeVisible();
    });
  }

  async verifyRunPanelVisible(): Promise<void> {
    await step('Verify the run panel is visible', async () => {
      await expect(this.run.panel).toBeVisible();
    });
  }

  async verifyStatus(status: string): Promise<void> {
    await step(`Verify the status badge reads "${status}"`, async () => {
      await expect(this.run.statusBadge).toHaveAttribute('data-status', status);
    });
  }

  async verifyPolling(): Promise<void> {
    await step('Verify polling is active (non-terminal status)', async () => {
      await expect(this.run.pollIndicator).toBeVisible();
    });
  }

  async verifyPollingStopped(): Promise<void> {
    await step('Verify polling has stopped (terminal status)', async () => {
      await expect(this.run.pollStopped).toBeVisible();
    });
  }

  async verifyPollIndicatorNeverShown(): Promise<void> {
    await step('Verify the live poll indicator never appeared', async () => {
      await expect(this.run.pollIndicator).not.toBeAttached();
    });
  }

  async verifyNoCheckpointOrErrorLingers(): Promise<void> {
    await step('Verify no checkpoint or error UI lingers in the done state', async () => {
      await expect(this.poDeep.panel).not.toBeAttached();
      await expect(this.error.panel).not.toBeAttached();
    });
  }

  async startOver(): Promise<void> {
    await step('Click start over', async () => {
      await this.run.clickStartOver();
    });
  }
}
