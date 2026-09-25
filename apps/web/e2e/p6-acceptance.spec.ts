/**
 * P6 acceptance walkthrough (manual gate; skipped unless a seeded learner is supplied):
 *
 *   Dashboard / Skill Detail -> VERIFY recommendation -> Verification Center -> challenge
 *   -> refresh while IN_PROGRESS (resume) -> submit -> evaluation / result
 *   -> Skill Detail VERIFIED with its updated explanation, debt and recommendation
 *
 * The learner is created and seeded by services/backend/scripts/acceptance_p6.py (local, scripted
 * fake provider) or acceptance_p6_hosted.py (hosted). A worker must run while this spec runs
 * (`acceptance_p6.py worker` locally; the backend's in-process worker on hosted) so the check is
 * generated and graded. Credentials and expectations come from git-ignored files
 * (P6_ACCEPTANCE_EMAIL / _PASSWORD / _EXPECT). Screenshots go to P6_ACCEPTANCE_OUT.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect as baseExpect, test, type Page } from '@playwright/test';

// Generation and grading run in the background worker: allow them time.
const expect = baseExpect.configure({ timeout: 45_000 });

interface Expectations {
  skill_id: string;
  skill_name: string;
  /** The mastery state before the check (from the fixture). */
  state_before: string;
  /** The state right after the check (VERIFIED when the fixture lets one pass cross the gates). */
  state_after?: string;
  /** The correct answer (MCQ option keys, or a written/numeric answer). */
  answer: { selected?: string[]; text?: string };
}

const email = process.env.P6_ACCEPTANCE_EMAIL;
const password = process.env.P6_ACCEPTANCE_PASSWORD;
const expectFile = process.env.P6_ACCEPTANCE_EXPECT;
const out = process.env.P6_ACCEPTANCE_OUT ?? join(__dirname, '..', '..', '..', 'test-results', 'p6-acceptance');

test.skip(
  !email || !password || !expectFile,
  'P6 acceptance needs a learner seeded by services/backend/scripts/acceptance_p6*.py',
);

const shot = async (page: Page, name: string) => {
  await expect(page.getByTestId('page-loading')).toHaveCount(0);
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true });
};

test('P6 verification loop: recommendation -> check -> result -> verified skill', async ({ page }) => {
  test.setTimeout(420_000);
  const x = JSON.parse(readFileSync(expectFile!, 'utf-8')) as Expectations;

  await page.goto('/sign-in?next=/dashboard');
  await page.getByLabel('Email').fill(email!);
  await page.getByLabel('Password').fill(password!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // 1. Dashboard: the VERIFY recommendation leads to a check.
  const verify = page.locator('[data-testid="recommendation"][data-type="VERIFY"]').filter({ hasText: x.skill_name });
  await expect(verify).toHaveCount(1);
  await expect(verify.getByTestId('recommendation-verify')).toBeVisible();
  await shot(page, '01-dashboard-verify');

  // 2. Skill Detail: the same recommendation, with a useful action.
  await page.goto(`/skills/${x.skill_id}`);
  await expect(page.getByTestId('mastery-panel')).toHaveAttribute('data-state', x.state_before);
  const action = page.getByTestId('skill-recommendation').getByTestId('recommendation-verify');
  await expect(action).toContainText('Start the check');
  await shot(page, '02-skill-detail-before');
  await action.click();
  // The action opens the Verification Center, or the check itself once it has been planned.
  await expect(page).toHaveURL(/\/verifications(\/[0-9a-f-]{36})?$/);

  // 3. Verification Center: planned (being prepared), then READY once the worker generated it.
  await page.goto('/verifications');
  const session = page.getByTestId('verification-session').filter({ hasText: x.skill_name });
  await expect(session).toHaveAttribute('data-status', /PREPARING|READY/);
  await shot(page, '03-center-planned');
  await expect(session).toHaveAttribute('data-status', 'READY', { timeout: 90_000 });
  await shot(page, '04-center-ready');
  await session.getByTestId('verification-open').click();

  // 4. The challenge: start, answer, then reload while IN_PROGRESS - it resumes with the draft.
  await expect(page).toHaveURL(/\/verifications\/[0-9a-f-]{36}$/);
  await page.getByTestId('verification-start').click();
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'IN_PROGRESS');
  const prompt = await page.getByTestId('challenge-prompt').innerText();
  const option = (key: string) => page.locator(`input[name="selected"][value="${key}"]`);
  if (x.answer.selected) {
    for (const key of x.answer.selected) await option(key).check();
  } else {
    await page.getByTestId('challenge-answer').fill(x.answer.text ?? '');
  }
  await shot(page, '05-challenge-in-progress');
  await page.reload();
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'IN_PROGRESS');
  await expect(page.getByTestId('challenge-prompt')).toHaveText(prompt);
  if (x.answer.selected) {
    for (const key of x.answer.selected) await expect(option(key)).toBeChecked();
  } else {
    await expect(page.getByTestId('challenge-answer')).toHaveValue(x.answer.text ?? '');
  }
  await shot(page, '06-challenge-resumed');

  // 5. Submit once (the button disables while sending), then the evaluation and the result.
  await page.getByTestId('challenge-submit').click();
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', /SUBMITTED|EVALUATED/);
  await shot(page, '07-submitted');
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'EVALUATED', { timeout: 90_000 });
  await expect(page.getByTestId('verification-result')).toHaveAttribute('data-passed', 'true');
  await expect(page.getByTestId('submitted-answer')).toContainText(x.answer.selected?.join(', ') ?? x.answer.text ?? '');
  await shot(page, '08-result');

  // 6. Skill Detail: VERIFIED (or the engine's predicted state), explained; debt factor updated.
  await page.getByTestId('result-skill-link').click();
  const after = x.state_after ?? 'VERIFIED';
  await expect(page.getByTestId('mastery-panel')).toHaveAttribute('data-state', after);
  await expect(page.locator('[data-testid="debt-factor"][data-code="VERIFICATION"]')).toContainText(
    'You recently passed a check.',
  );
  if (after !== 'VERIFIED') {
    // The pass alone did not cross the gates: the Why? shows which one is still open.
    await shot(page, '09-skill-detail-after-pass');
    return;
  }
  await expect(page.getByTestId('mastery-detail')).toContainText('You passed a recent SkillMirror check');
  await expect(page.getByTestId('skill-recommendation')).toContainText('A recent check confirmed this skill.');
  await page.getByTestId('mastery-why').locator('summary').click();
  await expect(page.locator('[data-testid="mastery-gate"][data-met="true"]').filter({ hasText: 'A recent SkillMirror check passed' })).toHaveCount(1);
  await shot(page, '09-skill-detail-verified');

  // 7. Dashboard: the skill counts as verified; the check is in the Verification Center history.
  await page.goto('/dashboard');
  await expect(page.locator('[data-testid="state-count"][data-state="VERIFIED"] dd')).toHaveText('1');
  await shot(page, '10-dashboard-after');
  await page.goto('/verifications');
  await expect(page.getByTestId('verifications-completed').getByTestId('verification-session')).toHaveAttribute(
    'data-status',
    'PASSED',
  );
  await shot(page, '11-center-completed');
});
