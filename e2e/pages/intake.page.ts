import type { Locator, Page } from '@playwright/test';

// The intake view: the hero + the requirement-entry panel shown before a run
// starts (and again after start-over). Owns the textarea, the run button, the
// file-upload input, and the upload status/error read-outs.
export default class IntakePage {
  readonly hero: Locator;
  readonly panel: Locator;
  readonly textarea: Locator;
  readonly runButton: Locator;
  readonly fileInput: Locator;
  readonly uploadStatus: Locator;
  readonly uploadError: Locator;

  constructor(private readonly page: Page) {
    this.hero = page.getByTestId('hero');
    this.panel = page.getByTestId('intake-panel');
    this.textarea = page.getByTestId('intake-textarea');
    this.runButton = page.getByTestId('intake-run');
    this.fileInput = page.getByTestId('intake-file');
    this.uploadStatus = page.getByTestId('intake-upload-status');
    this.uploadError = page.getByTestId('intake-error');
  }

  async enterRequirement(text: string): Promise<void> {
    await this.textarea.fill(text);
  }

  async run(): Promise<void> {
    await this.runButton.click();
  }

  // The file input is sr-only; setInputFiles drives it directly, never a click
  // on the hidden element.
  async uploadFile(
    file: string | { name: string; mimeType: string; buffer: Buffer },
  ): Promise<void> {
    await this.fileInput.setInputFiles(file);
  }
}
