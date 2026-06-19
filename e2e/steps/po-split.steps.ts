import { expect } from '@playwright/test';
import { step } from '@common/step';
import type PoSplitCheckpointPage from '@pages/po-split-checkpoint.page';
import type PoDeepCheckpointPage from '@pages/po-deep-checkpoint.page';

// Split-mode PO checkpoint workflows + assertions: the split checkpoint
// appearance (and that the deep checkpoint is NOT present), per-row defaults,
// dropping a row, renaming a story, and submitting the selection.
export default class PoSplitSteps {
  constructor(
    private readonly split: PoSplitCheckpointPage,
    private readonly deep: PoDeepCheckpointPage,
  ) {}

  async verifySplitCheckpoint(storyTitles: readonly string[]): Promise<void> {
    await step('Verify the split PO checkpoint with one row per implied story', async () => {
      await expect(this.split.panel).toBeVisible();
      await expect(this.deep.panel).not.toBeAttached();
      for (let i = 0; i < storyTitles.length; i += 1) {
        await expect(this.split.row(i)).toBeVisible();
      }
      await expect(this.split.summary).toHaveText(
        `${storyTitles.length} of ${storyTitles.length} stories will become placeholders.`,
      );
    });
  }

  async verifyRowDefaults(firstTitle: string): Promise<void> {
    await step('Verify every row is kept by default and seeded from the Analyst output', async () => {
      await expect(this.split.keep(0)).toBeChecked();
      await expect(this.split.row(0)).toHaveAttribute('data-kept', 'true');
      await expect(this.split.title(0)).toHaveValue(firstTitle);
    });
  }

  async dropRow(index: number, expectedSummary: string): Promise<void> {
    await step(`Untick row ${index}; it drops from the selection and its title locks`, async () => {
      await this.split.toggleKeep(index);
      await expect(this.split.row(index)).toHaveAttribute('data-kept', 'false');
      await expect(this.split.title(index)).toBeDisabled();
      await expect(this.split.summary).toHaveText(expectedSummary);
    });
  }

  async dropRowWithoutAssert(index: number): Promise<void> {
    await step(`Untick row ${index}`, async () => {
      await this.split.toggleKeep(index);
    });
  }

  async renameStory(index: number, title: string): Promise<void> {
    await step(`Rename row ${index} to "${title}"`, async () => {
      await this.split.renameStory(index, title);
      await expect(this.split.title(index)).toHaveValue(title);
    });
  }

  async renameStoryWithoutAssert(index: number, title: string): Promise<void> {
    await step(`Rename row ${index} to "${title}"`, async () => {
      await this.split.renameStory(index, title);
    });
  }

  async submitSelection(): Promise<void> {
    await step('Submit the split selection', async () => {
      await this.split.submitSelection();
    });
  }

  async verifySplitCheckpointGone(): Promise<void> {
    await step('Verify the split PO checkpoint has unmounted', async () => {
      await expect(this.split.panel).not.toBeAttached();
    });
  }
}
