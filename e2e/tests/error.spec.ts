import test from '@common/base';

const REQUIREMENT =
  'As an admin I want to manage users: reset passwords, assign roles, view an audit log, and bulk-import users.';

test.describe('error flow', () => {
  test('analyst failure surfaces error panel with details disclosure', async ({
    intakeSteps,
    runSteps,
    errorSteps,
  }) => {
    await intakeSteps.submitRequirement(REQUIREMENT);

    // POST -> error is a TERMINAL initial status: the run panel mounts straight
    // into the stopped-polling state; the live poll indicator never appears.
    await runSteps.verifyRunPanelVisible();
    await runSteps.verifyStatus('error');
    await runSteps.verifyPollingStopped();
    await runSteps.verifyPollIndicatorNeverShown();

    await errorSteps.verifyErrorPanel('requirements_analyst', 'RuntimeError');
    await errorSteps.verifyNoArtifactPanels();

    await errorSteps.revealDetails();
    await errorSteps.hideDetails();

    await errorSteps.startOver();
    await intakeSteps.verifyIntakeView();
  });
});
