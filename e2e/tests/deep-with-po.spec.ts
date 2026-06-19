import test from '@common/base';

// Flow: deep_with_po (fake backend on :8002). State machine —
// POST -> paused_po (deep, one critical ambiguity) -> resume {answers}
// -> paused_review -> accept/override -> done.
const REQUIREMENT =
  'As an admin I want to manage users: reset passwords, assign roles, view an audit log, and bulk-import users.';

test.describe('deep_with_po', () => {
  test('happy path: PO answers -> review accept -> done', async ({
    intakeSteps,
    runSteps,
    poDeepSteps,
    storyReviewSteps,
    resultSteps,
    exportSteps,
  }) => {
    await intakeSteps.submitRequirement(REQUIREMENT);

    // POST is synchronous in the fake backend: the first status the UI shows
    // is paused_po, never a transient running state.
    await runSteps.verifyStatus('paused_po');
    await poDeepSteps.verifyDeepCheckpoint('soft-deleted');
    await runSteps.verifyPolling();

    await poDeepSteps.answerAndSubmit('Active users only.');

    // Resume applies the response state directly — transition is immediate.
    await poDeepSteps.verifyDeepCheckpointGone();
    await runSteps.verifyStatus('paused_review');
    await storyReviewSteps.verifyReviewBranches();
    await runSteps.verifyPolling();

    await storyReviewSteps.acceptAsIs();

    await runSteps.verifyStatus('done');
    await runSteps.verifyPollingStopped();
    await resultSteps.verifyResultVisible();
    await exportSteps.verifyDeepMode();
  });

  test('override form opens and cancels', async ({
    intakeSteps,
    runSteps,
    poDeepSteps,
    storyReviewSteps,
  }) => {
    // Precondition drive: intake -> PO answer -> paused_review.
    await intakeSteps.submitRequirement(REQUIREMENT);
    await runSteps.verifyStatus('paused_po');
    await poDeepSteps.answerAndSubmitWithoutAssert('Active users only.');
    await runSteps.verifyStatus('paused_review');

    await storyReviewSteps.openOverrideForm();
    await storyReviewSteps.verifyOverrideFormOpen();

    await storyReviewSteps.cancelOverrideForm();
    await storyReviewSteps.verifyOverrideFormClosed();
  });

  test('override submit reaches done', async ({
    intakeSteps,
    runSteps,
    poDeepSteps,
    storyReviewSteps,
    resultSteps,
  }) => {
    // Precondition drive: intake -> PO answer -> paused_review.
    await intakeSteps.submitRequirement(REQUIREMENT);
    await runSteps.verifyStatus('paused_po');
    await poDeepSteps.answerAndSubmitWithoutAssert('Active users only.');
    await runSteps.verifyStatus('paused_review');

    await storyReviewSteps.openOverrideForm();
    await storyReviewSteps.submitOverride(
      'As a compliance officer, I want to export only active users.',
      'Auditable, GDPR-safe user exports.',
    );

    await runSteps.verifyStatus('done');
    await resultSteps.verifyResultVisible();
    // The override description flows into the composed artifact.
    await resultSteps.verifyResultContains('compliance officer');
  });
});
