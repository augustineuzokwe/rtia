import type { Locator, Page } from '@playwright/test';

// The export panel shown on both terminal artifact states. data-mode switches
// between 'deep' (push the single story to the backlog) and 'split' (create
// follow-up issues from the kept placeholders). Owns the dry-run toggle, the
// GitHub repo field, the submit button, and the fanned-out export results
// (export-result-N, positional over the response array).
export default class ExportPage {
  readonly panel: Locator;
  readonly submit: Locator;
  readonly dryRun: Locator;
  readonly githubRepo: Locator;
  readonly results: Locator;

  constructor(private readonly page: Page) {
    this.panel = page.getByTestId('export-panel');
    this.submit = page.getByTestId('export-submit');
    this.dryRun = page.getByTestId('export-dry-run');
    this.githubRepo = page.getByTestId('github-repo');
    this.results = page.getByTestId('export-results');
  }

  result(index: number): Locator {
    return this.page.getByTestId(`export-result-${index}`);
  }

  async setGithubRepo(repo: string): Promise<void> {
    await this.githubRepo.fill(repo);
  }

  async submitExport(): Promise<void> {
    await this.submit.click();
  }
}
