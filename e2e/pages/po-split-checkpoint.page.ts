import type { Locator, Page } from '@playwright/test';

// The split-mode PO checkpoint (paused_po, mode=split): the Analyst's implied
// stories, one row each, every row kept by default with an editable title and
// a keep checkbox. A summary line counts the kept rows; submit fans the
// selection out to placeholders (terminal done_split). Rows/keeps/titles are
// indexed (split-row-N / split-keep-N / split-title-N).
export default class PoSplitCheckpointPage {
  readonly panel: Locator;
  readonly summary: Locator;
  readonly submit: Locator;

  constructor(private readonly page: Page) {
    this.panel = page.getByTestId('po-checkpoint-split');
    this.summary = page.getByTestId('split-summary');
    this.submit = page.getByTestId('split-submit');
  }

  row(index: number): Locator {
    return this.page.getByTestId(`split-row-${index}`);
  }

  keep(index: number): Locator {
    return this.page.getByTestId(`split-keep-${index}`);
  }

  title(index: number): Locator {
    return this.page.getByTestId(`split-title-${index}`);
  }

  async toggleKeep(index: number): Promise<void> {
    await this.keep(index).click();
  }

  async renameStory(index: number, title: string): Promise<void> {
    await this.title(index).clear();
    await this.title(index).fill(title);
  }

  async submitSelection(): Promise<void> {
    await this.submit.click();
  }
}
