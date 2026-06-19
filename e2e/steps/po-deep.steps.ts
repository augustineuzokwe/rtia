import { expect } from '@playwright/test';
import { step } from '@common/step';
import type PoDeepCheckpointPage from '@pages/po-deep-checkpoint.page';

// Deep-mode PO checkpoint workflows + assertions: the checkpoint appearance,
// answering the critical ambiguity, and confirming it unmounts on submit.
export default class PoDeepSteps {
  constructor(private readonly checkpoint: PoDeepCheckpointPage) {}

  async verifyDeepCheckpoint(questionContains: string): Promise<void> {
    await step('Verify the deep PO checkpoint and its critical question', async () => {
      await expect(this.checkpoint.panel).toBeVisible();
      await expect(this.checkpoint.question(0)).toBeVisible();
      await expect(this.checkpoint.question(0)).toContainText(questionContains);
      await expect(this.checkpoint.answer(0)).toHaveValue('');
      await expect(this.checkpoint.submit).toBeEnabled();
    });
  }

  async answerAndSubmit(answer: string): Promise<void> {
    await step('Answer the PO question and submit', async () => {
      await this.checkpoint.fillAnswer(0, answer);
      await expect(this.checkpoint.answer(0)).toHaveValue(answer);
      await this.checkpoint.submitAnswers();
    });
  }

  async answerAndSubmitWithoutAssert(answer: string): Promise<void> {
    await step('Answer the PO question and submit', async () => {
      await this.checkpoint.fillAnswer(0, answer);
      await this.checkpoint.submitAnswers();
    });
  }

  async verifyDeepCheckpointGone(): Promise<void> {
    await step('Verify the deep PO checkpoint has unmounted', async () => {
      await expect(this.checkpoint.panel).not.toBeAttached();
    });
  }
}
