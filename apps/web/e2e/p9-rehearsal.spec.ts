/**
 * P9 fresh-start rehearsal (manual gate; skipped unless REHEARSAL_* is set): the prepared demo
 * learner on the running demo (`npm run demo`, hosted Supabase, the Flash-Lite worker).
 *
 *   first page -> sign in -> Dashboard "Needs your attention" -> Skill Map -> Verification Center
 *   (the worker writes ONE real Flash-Lite challenge) -> start -> answer -> deterministic grade ->
 *   VERIFICATION evidence -> ledger / recommendation update -> the student cannot open /teacher or
 *   /admin -> diagnostics only on /settings.
 *
 * The rehearsal takes the pass path: `demo_verify_fixture.py answer` reads the open challenge's
 * key server-side (never do this in front of an audience). Timings go to REHEARSAL_OUT.
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test } from '@playwright/test';

const env = (name: string) => process.env[name];
const out = env('REHEARSAL_OUT') ?? join(__dirname, '..', '..', '..', 'test-results', 'p9-rehearsal');

test('rehearsal: demo learner -> real Flash-Lite check -> grade -> ledger; roles; diagnostics', async ({ page }) => {
  test.skip(!env('REHEARSAL_EMAIL') || !env('REHEARSAL_PASSWORD') || !env('REHEARSAL_PYTHON'), 'needs REHEARSAL_*');
  test.setTimeout(300_000);
  const timings: Record<string, number> = {};
  const time = async <T>(name: string, step: () => Promise<T>) => {
    const started = Date.now();
    const result = await step();
    timings[name] = Math.round((Date.now() - started) / 100) / 10;
    return result;
  };

  await time('first_page_s', async () => {
    await page.goto('/sign-in');
    await expect(page.getByLabel('Email')).toBeVisible();
  });
  await time('sign_in_to_dashboard_s', async () => {
    await page.getByLabel('Email').fill(env('REHEARSAL_EMAIL')!);
    await page.getByLabel('Password').fill(env('REHEARSAL_PASSWORD')!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByTestId('dashboard-attention-list')).toBeVisible();
  });
  // Diagnostics are not on the learner dashboard.
  await expect(page.getByTestId('user-id')).toHaveCount(0);
  await expect(page.getByTestId('dashboard-knowledge')).toContainText('not a low score');

  await time('skill_map_s', async () => {
    await page.goto('/skills');
    await expect(page.getByTestId('topic-summary').first()).toBeVisible();
  });

  // The Verification Center plans the check; the demo worker writes ONE real challenge.
  await time('verification_generation_s', async () => {
    await page.goto('/verifications');
    await expect(page.getByTestId('verifications-ready').getByTestId('verification-open')).toBeVisible({ timeout: 120_000 });
  });
  await page.getByTestId('verifications-ready').getByTestId('verification-open').click();
  await page.getByTestId('verification-start').click();
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'IN_PROGRESS');
  await expect(page.getByTestId('challenge-prompt')).toBeVisible();

  const key = JSON.parse(
    execFileSync(env('REHEARSAL_PYTHON')!, ['scripts/demo_verify_fixture.py', 'answer'], {
      cwd: env('REHEARSAL_BACKEND_DIR') ?? join(__dirname, '..', '..', '..', 'services', 'backend'),
      encoding: 'utf8',
    })
      .trim()
      .split('\n')
      .pop()!,
  ) as { assessment_type: string; answer: string };
  if (key.assessment_type === 'mcq') {
    for (const choice of key.answer.split(',')) await page.locator(`input[name="selected"][value="${choice.trim()}"]`).check();
  } else {
    await page.getByTestId('challenge-answer').fill(key.answer);
  }
  await time('grading_s', async () => {
    await page.getByTestId('challenge-submit').click();
    await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'EVALUATED', { timeout: 120_000 });
  });
  await expect(page.getByTestId('verification-result')).toHaveAttribute('data-passed', 'true');
  await page.getByTestId('result-skill-link').click();
  await expect(page.getByTestId('evidence-item').filter({ hasText: 'SkillMirror check' })).toHaveCount(1);
  await expect(page.getByTestId('recommendation-verify')).toHaveCount(0);
  await page.goto('/dashboard');
  await expect(page.getByTestId('attention-empty')).toBeVisible();

  // A student cannot open the teacher or admin areas.
  for (const area of ['/teacher', '/admin', '/admin/benchmark']) {
    await page.goto(area);
    await expect(page.getByTestId('forbidden-panel')).toBeVisible();
  }
  await page.goto('/settings');
  await expect(page.getByTestId('user-id')).toBeVisible();

  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, 'timings.json'), JSON.stringify(timings, null, 2));
  console.log(JSON.stringify(timings));
});
