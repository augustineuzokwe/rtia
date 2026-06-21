// Intake file-upload flow. The `upload` project reuses the deep_clean backend
// (:8001) — see playwright.config.ts — because upload extraction is a
// frontend/endpoint concern independent of which pipeline scenario runs after.
//
// The intake file input is sr-only; the page object drives it ONLY via
// setInputFiles (Playwright sets files directly, no click on a hidden input).
import { fileURLToPath } from 'node:url';

import test from '@common/base';

const PDF_FIXTURE = fileURLToPath(
  new URL('../data/files/req.pdf', import.meta.url),
);

test.describe('intake file upload', () => {
  test('markdown upload fills the textarea', async ({ intakeSteps }) => {
    await intakeSteps.verifyTextareaEmpty();

    await intakeSteps.uploadMarkdown(
      'req.md',
      '# Feature: CSV export\n\nAs a user I want to export my data as CSV so that I can analyse it offline.',
    );

    await intakeSteps.verifyMarkdownExtracted(/export my data as CSV/);
  });

  test('pdf upload extracts text', async ({ intakeSteps }) => {
    await intakeSteps.uploadPdf(PDF_FIXTURE);

    await intakeSteps.verifyPdfExtracted(/audit log as CSV/);
  });

  test('unsupported file type is rejected', async ({ intakeSteps }) => {
    await intakeSteps.uploadUnsupported('notes.txt', 'plain text');

    await intakeSteps.verifyUnsupportedRejected();
  });
});
