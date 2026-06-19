import { expect } from '@playwright/test';
import { step } from '@common/step';
import type StoryReviewPage from '@pages/story-review.page';

// Story-review checkpoint workflows + assertions: the review appearance, the
// accept-as-is branch, and the override-form open/cancel/submit branch.
export default class StoryReviewSteps {
  constructor(private readonly review: StoryReviewPage) {}

  async verifyReviewCheckpoint(): Promise<void> {
    await step('Verify the story-review checkpoint is shown with a non-empty preview', async () => {
      await expect(this.review.panel).toBeVisible();
      await expect(this.review.preview).not.toBeEmpty();
    });
  }

  async verifyReviewBranches(): Promise<void> {
    await step('Verify both review branches are offered and the override form is closed', async () => {
      await expect(this.review.panel).toBeVisible();
      await expect(this.review.preview).not.toBeEmpty();
      await expect(this.review.acceptAsIs).toBeVisible();
      await expect(this.review.openOverride).toBeVisible();
      await expect(this.review.overrideForm).not.toBeAttached();
    });
  }

  async acceptAsIs(): Promise<void> {
    await step('Accept the story as-is', async () => {
      await this.review.accept();
    });
  }

  async openOverrideForm(): Promise<void> {
    await step('Open the override form', async () => {
      await this.review.openOverrideForm();
    });
  }

  async verifyOverrideFormOpen(): Promise<void> {
    await step('Verify the override form is open with its fields and accept-as-is hidden', async () => {
      await expect(this.review.overrideForm).toBeVisible();
      await expect(this.review.overrideDescription).toBeVisible();
      await expect(this.review.overrideObjective).toBeVisible();
      await expect(this.review.cancelOverride).toBeVisible();
      await expect(this.review.submitOverride).toBeVisible();
      await expect(this.review.acceptAsIs).not.toBeAttached();
    });
  }

  async cancelOverrideForm(): Promise<void> {
    await step('Cancel the override form', async () => {
      await this.review.cancelOverrideForm();
    });
  }

  async verifyOverrideFormClosed(): Promise<void> {
    await step('Verify the override form closed and accept-as-is is back', async () => {
      await expect(this.review.acceptAsIs).toBeVisible();
      await expect(this.review.overrideForm).not.toBeAttached();
    });
  }

  async submitOverride(description: string, objective: string): Promise<void> {
    await step('Fill and submit the override form', async () => {
      await this.review.fillOverride(description, objective);
      await this.review.submitOverrideForm();
    });
  }
}
