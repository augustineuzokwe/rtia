import type { Locator, Page } from '@playwright/test';

// The story-review checkpoint (paused_review): a story preview plus two
// branches — accept-as-is, or open the override form (description + objective)
// and submit/cancel it.
export default class StoryReviewPage {
  readonly panel: Locator;
  readonly preview: Locator;
  readonly acceptAsIs: Locator;
  readonly openOverride: Locator;
  readonly overrideForm: Locator;
  readonly overrideDescription: Locator;
  readonly overrideObjective: Locator;
  readonly cancelOverride: Locator;
  readonly submitOverride: Locator;

  constructor(private readonly page: Page) {
    this.panel = page.getByTestId('story-review');
    this.preview = page.getByTestId('story-preview');
    this.acceptAsIs = page.getByTestId('accept-as-is');
    this.openOverride = page.getByTestId('open-override');
    this.overrideForm = page.getByTestId('story-override-form');
    this.overrideDescription = page.getByTestId('override-description');
    this.overrideObjective = page.getByTestId('override-objective');
    this.cancelOverride = page.getByTestId('cancel-override');
    this.submitOverride = page.getByTestId('submit-override');
  }

  async accept(): Promise<void> {
    await this.acceptAsIs.click();
  }

  async openOverrideForm(): Promise<void> {
    await this.openOverride.click();
  }

  async cancelOverrideForm(): Promise<void> {
    await this.cancelOverride.click();
  }

  async fillOverride(description: string, objective: string): Promise<void> {
    await this.overrideDescription.fill(description);
    await this.overrideObjective.fill(objective);
  }

  async submitOverrideForm(): Promise<void> {
    await this.submitOverride.click();
  }
}
