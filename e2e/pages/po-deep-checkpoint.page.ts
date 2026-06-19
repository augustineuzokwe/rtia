import type { Locator, Page } from '@playwright/test';

// The deep-mode PO checkpoint (paused_po, mode=deep): one or more critical
// ambiguities, each a question + answer textarea, resolved by submit. Question
// and answer locators are indexed (data-testid=po-question-N / po-answer-N).
export default class PoDeepCheckpointPage {
  readonly panel: Locator;
  readonly submit: Locator;

  constructor(private readonly page: Page) {
    this.panel = page.getByTestId('po-checkpoint-deep');
    this.submit = page.getByTestId('po-submit');
  }

  question(index: number): Locator {
    return this.page.getByTestId(`po-question-${index}`);
  }

  answer(index: number): Locator {
    return this.page.getByTestId(`po-answer-${index}`);
  }

  async fillAnswer(index: number, text: string): Promise<void> {
    await this.answer(index).fill(text);
  }

  async submitAnswers(): Promise<void> {
    await this.submit.click();
  }
}
