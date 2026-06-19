import test from '@common/base';

// Flow: deep_clean (fake backend on :8001). POST /pipeline is synchronous and
// auto-passes the PO checkpoint, landing on the story-review checkpoint
// (paused_review is ALWAYS the first status — there is no direct-to-done).
// Accepting the story as-is resolves the review checkpoint and the deep flow
// runs through to done.
const REQUIREMENT =
  'As an admin I want to manage users: reset passwords, assign roles, view an audit log, and bulk-import users.';

test.describe('deep_clean: intake to review to done', () => {
  test('happy path: intake -> review -> done -> start over', async ({
    intakeSteps,
    runSteps,
    storyReviewSteps,
    resultSteps,
    exportSteps,
  }) => {
    await intakeSteps.verifyIntakeView();
    await intakeSteps.verifyRunGatedOnRequirement(REQUIREMENT);
    await intakeSteps.startRun();

    await runSteps.verifyRunViewTookOver();
    await runSteps.verifyStatus('paused_review');
    await storyReviewSteps.verifyReviewCheckpoint();
    await runSteps.verifyPolling();

    await storyReviewSteps.acceptAsIs();
    await runSteps.verifyStatus('done');
    await runSteps.verifyPollingStopped();
    await resultSteps.verifyResultPanel();
    await exportSteps.verifyDeepExportPanel();
    await runSteps.verifyNoCheckpointOrErrorLingers();

    await runSteps.startOver();
    await intakeSteps.verifyReturnedToCleanIntake();
  });
});
