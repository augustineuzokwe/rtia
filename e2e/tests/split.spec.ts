import test from '@common/base';

// Split flow (fake backend :8003). The Analyst emits exactly four implied
// stories, in order, and the POST /pipeline is synchronous — the first status
// the UI shows is paused_po (mode=split). Resuming with a selection terminates
// the thread at done_split (no result-panel; the export-panel switches to
// split mode + the /export-deferred endpoint).
const REQUIREMENT =
  'As an admin I want to manage users: reset passwords, assign roles, view an audit log, and bulk-import users.';

const STORY_TITLES = [
  'Self-service password reset',
  'Admin assigns role to user',
  'Admin views user audit log',
  'Bulk user import from CSV',
] as const;

const RENAMED_TITLE = 'Password reset self-service';

test.describe('split flow', () => {
  test('select, rename, submit -> done_split', async ({
    intakeSteps,
    runSteps,
    poSplitSteps,
    exportSteps,
    resultSteps,
  }) => {
    await intakeSteps.submitRequirement(REQUIREMENT);

    // The synchronous POST lands directly on the split PO checkpoint.
    await runSteps.verifyStatus('paused_po');
    await poSplitSteps.verifySplitCheckpoint(STORY_TITLES);
    await runSteps.verifyPolling();

    await poSplitSteps.verifyRowDefaults(STORY_TITLES[0]);

    // Untick row 1 — it drops out of the selection and its title locks.
    await poSplitSteps.dropRow(1, '3 of 4 stories will become placeholders.');

    // Rename the first kept story before fan-out.
    await poSplitSteps.renameStory(0, RENAMED_TITLE);

    // Resume — transitions immediately to the terminal split state.
    await poSplitSteps.submitSelection();

    await poSplitSteps.verifySplitCheckpointGone();
    await runSteps.verifyStatus('done_split');
    await runSteps.verifyPollingStopped();
    await exportSteps.verifySplitExportPanel();
    await resultSteps.verifyResultPanelAbsent();
  });

  test('dry-run deferred export creates one result per KEPT story', async ({
    intakeSteps,
    runSteps,
    poSplitSteps,
    exportSteps,
  }) => {
    await intakeSteps.submitRequirement(REQUIREMENT);

    await runSteps.verifyStatus('paused_po');
    await poSplitSteps.verifySplitCheckpoint(STORY_TITLES);

    // Same selection as T1: drop row 1, rename row 0, keep rows 0/2/3.
    await poSplitSteps.dropRowWithoutAssert(1);
    await poSplitSteps.renameStoryWithoutAssert(0, RENAMED_TITLE);
    await poSplitSteps.submitSelection();

    await runSteps.verifyStatus('done_split');

    // Dry-run is the default — a missing credential never creates a live issue.
    await exportSteps.verifyDryRunDefault();
    await exportSteps.runGithubDryRunExport('acme/backlog');

    // Three kept stories fan out to result indices 0..2; the unticked story is
    // excluded so index 3 never renders. The renamed title flowed through.
    await exportSteps.verifyDeferredResults(3, RENAMED_TITLE);
  });
});
