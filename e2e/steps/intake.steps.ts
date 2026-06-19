import { expect } from '@playwright/test';
import { step } from '@common/step';
import type IntakePage from '@pages/intake.page';
import type RunPage from '@pages/run.page';

// Intake-view workflows + their assertions: the initial state, the run gate,
// starting a run, and the file-upload paths (markdown / pdf / unsupported).
export default class IntakeSteps {
  constructor(
    private readonly intake: IntakePage,
    private readonly run: RunPage,
  ) {}

  async verifyIntakeView(): Promise<void> {
    await step('Verify the intake view is shown and the run panel is not mounted', async () => {
      await expect(this.intake.hero).toBeVisible();
      await expect(this.intake.panel).toBeVisible();
      await expect(this.run.panel).not.toBeAttached();
    });
  }

  async verifyRunGatedOnRequirement(requirement: string): Promise<void> {
    await step('Verify the run button is gated on non-empty requirement text', async () => {
      await expect(this.intake.runButton).toBeDisabled();
      await this.intake.enterRequirement(requirement);
      await expect(this.intake.runButton).toBeEnabled();
    });
  }

  async enterRequirement(requirement: string): Promise<void> {
    await step('Enter the requirement text', async () => {
      await this.intake.enterRequirement(requirement);
    });
  }

  async startRun(): Promise<void> {
    await step('Start the run', async () => {
      await this.intake.run();
    });
  }

  async submitRequirement(requirement: string): Promise<void> {
    await step('Enter the requirement and start the run', async () => {
      await this.intake.enterRequirement(requirement);
      await this.intake.run();
    });
  }

  async verifyReturnedToCleanIntake(): Promise<void> {
    await step('Verify start-over returned to a clean intake view', async () => {
      await expect(this.intake.panel).toBeVisible();
      await expect(this.run.panel).not.toBeAttached();
      await expect(this.intake.hero).toBeVisible();
      await expect(this.intake.textarea).toHaveValue('');
    });
  }

  async verifyTextareaEmpty(): Promise<void> {
    await step('Verify the requirement textarea starts empty', async () => {
      await expect(this.intake.textarea).toHaveValue('');
    });
  }

  async uploadMarkdown(name: string, body: string): Promise<void> {
    await step('Upload a Markdown requirement file', async () => {
      await this.intake.uploadFile({
        name,
        mimeType: 'text/markdown',
        buffer: Buffer.from(body),
      });
    });
  }

  async uploadPdf(path: string): Promise<void> {
    await step('Upload a PDF requirement file', async () => {
      await this.intake.uploadFile(path);
    });
  }

  async uploadUnsupported(name: string, body: string): Promise<void> {
    await step('Upload an unsupported file type', async () => {
      await this.intake.uploadFile({
        name,
        mimeType: 'text/plain',
        buffer: Buffer.from(body),
      });
    });
  }

  async verifyMarkdownExtracted(expectedTextareaMatch: RegExp): Promise<void> {
    await step('Verify the Markdown upload populated the textarea', async () => {
      await expect(this.intake.uploadStatus).toBeVisible();
      await expect(this.intake.uploadStatus).toContainText('characters from Markdown');
      await expect(this.intake.textarea).toHaveValue(expectedTextareaMatch);
      await expect(this.intake.runButton).toBeEnabled();
      await expect(this.intake.uploadError).not.toBeAttached();
    });
  }

  async verifyPdfExtracted(expectedTextareaMatch: RegExp): Promise<void> {
    await step('Verify the PDF upload extracted text into the textarea', async () => {
      await expect(this.intake.uploadStatus).toBeVisible();
      await expect(this.intake.uploadStatus).toContainText('characters from PDF');
      await expect(this.intake.textarea).toHaveValue(expectedTextareaMatch);
      await expect(this.intake.runButton).toBeEnabled();
    });
  }

  async verifyUnsupportedRejected(): Promise<void> {
    await step('Verify the unsupported file is rejected and nothing is extracted', async () => {
      await expect(this.intake.uploadError).toBeVisible();
      await expect(this.intake.uploadError).toContainText('Unsupported file type');
      await expect(this.intake.uploadStatus).not.toBeAttached();
      await expect(this.intake.textarea).toHaveValue('');
      await expect(this.intake.runButton).toBeDisabled();
    });
  }
}
