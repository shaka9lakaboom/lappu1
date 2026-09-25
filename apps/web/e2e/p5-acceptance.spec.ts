/**
 * P5 acceptance walkthrough (manual gate; skipped unless a seeded learner is supplied):
 *
 *   dashboard -> Skill Map -> Skill Detail -> "Why?" -> Activity -> correction
 *   -> recomputed ledger -> updated recommendation
 *
 * The learner is created and seeded by services/backend/scripts/acceptance_p5.py (local) or
 * acceptance_p5_hosted.py (hosted, on an existing course graph); both use deterministic
 * fixtures and make no model call. They write the learner's credentials and an expectations
 * file (P5_ACCEPTANCE_EXPECT: state counts, skill names, debt band) to git-ignored files.
 * The web app must point at the same Supabase project and at a backend without GEMINI_API_KEY.
 * Screenshots go to P5_ACCEPTANCE_OUT (default ../../test-results/p5-acceptance).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect as baseExpect, test, type Page } from '@playwright/test';

// Against hosted Supabase a cold dev-server page can take several seconds.
const expect = baseExpect.configure({ timeout: 30_000 });

interface Expectations {
  counts: Record<string, number>;
  counts_after: Record<string, number>;
  names: Record<'for_loops' | 'while_loops' | 'comprehensions' | 'variables' | 'indexing', string>;
  topics: number;
  skill_rows: number;
  debt_band: string;
  debt_label: string;
  recommendations: number;
  recommendations_after: number;
  practice_after: number;
}

const email = process.env.P5_ACCEPTANCE_EMAIL;
const password = process.env.P5_ACCEPTANCE_PASSWORD;
const expectFile = process.env.P5_ACCEPTANCE_EXPECT;
const out = process.env.P5_ACCEPTANCE_OUT ?? join(__dirname, '..', '..', '..', 'test-results', 'p5-acceptance');

test.skip(
  !email || !password || !expectFile,
  'P5 acceptance needs a learner seeded by services/backend/scripts/acceptance_p5*.py',
);

const shot = async (page: Page, name: string) => {
  // Never capture a transient loading skeleton (e.g. a refresh right after a correction).
  await expect(page.getByTestId('page-loading')).toHaveCount(0);
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true });
};

test('P5 student experience: inspect every state, correct evidence, see the recomputed result', async ({ page }) => {
  test.setTimeout(300_000);
  const x = JSON.parse(readFileSync(expectFile!, 'utf-8')) as Expectations;
  const row = (name: string) => page.getByTestId('skill-map-row').filter({ hasText: name });
  const tile = (state: string) => page.locator(`[data-testid="state-count"][data-state="${state}"] dd`);
  const expectCounts = async (counts: Record<string, number>) => {
    for (const [state, n] of Object.entries(counts)) await expect(tile(state)).toHaveText(String(n));
  };

  // Sign in as the seeded learner.
  await page.goto('/sign-in?next=/dashboard');
  await page.getByLabel('Email').fill(email!);
  await page.getByLabel('Password').fill(password!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // 1. Dashboard: course card, state counts (UNKNOWN neutral, first), top recommendations.
  await expectCounts(x.counts);
  await expect(page.locator('[data-testid="state-count"][data-state="UNKNOWN"]')).toContainText('Not enough activity yet');
  await expect(page.locator('[data-testid="state-count"][data-state="UNKNOWN"]')).toHaveClass(/border-dashed/);
  await expect(page.getByTestId('dashboard-course-card')).toHaveCount(1);
  const recs = page.getByTestId('recommendation');
  await expect(recs).toHaveCount(x.recommendations);
  await expect(recs.first()).toHaveAttribute('data-type', 'VERIFY');
  await shot(page, '01-dashboard');

  // 2. Skill map: topic -> skills -> state; a skill without ledger row is UNKNOWN (neutral).
  await page.getByTestId('skill-map-link').click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(page.getByTestId('topic-group')).toHaveCount(x.topics);
  await expect(page.getByTestId('skill-map-row')).toHaveCount(x.skill_rows);
  await expect(page.locator('[data-testid="skill-map-row"][data-state="UNKNOWN"]')).toHaveCount(x.counts.UNKNOWN);
  const unknownRow = row(x.names.while_loops);
  await expect(unknownRow).toHaveAttribute('data-state', 'UNKNOWN');
  await expect(unknownRow.getByTestId('mastery-badge')).toHaveText('Not enough activity yet');
  await expect(unknownRow.getByTestId('mastery-badge')).toHaveAttribute('data-tone', 'neutral');
  await expect(row(x.names.for_loops)).toHaveAttribute('data-state', 'DEMONSTRATED');
  await expect(row(x.names.variables)).toHaveAttribute('data-state', 'EMERGING');
  await expect(row(x.names.indexing)).toHaveAttribute('data-state', 'DEVELOPING');
  await shot(page, '02-skill-map');

  // 3. Skill detail of the demonstrated skill, with the mastery "Why?" and an evidence "Why?".
  await row(x.names.for_loops).getByRole('link').click();
  await expect(page.getByTestId('mastery-panel')).toHaveAttribute('data-state', 'DEMONSTRATED');
  await expect(page.getByTestId('mastery-headline')).toHaveText('Your recent independent evidence supports this skill.');
  await page.getByTestId('mastery-why').locator('summary').click();
  await expect(page.locator('[data-testid="mastery-gate"][data-met="true"]')).toHaveCount(4);
  await expect(page.getByTestId('evidence-item')).toHaveCount(5);
  await expect(page.locator('[data-testid="evidence-item"][data-excluded="true"]')).toHaveCount(1);
  const firstCounted = page.locator('[data-testid="evidence-item"][data-excluded="false"]').first();
  await firstCounted.getByTestId('evidence-why').locator('summary').click();
  await expect(firstCounted.getByTestId('evidence-span').first()).toBeVisible();
  await expect(firstCounted.getByTestId('mapping-confidence')).toHaveText('90%');
  await expect(firstCounted.getByTestId('attribution-confidence')).toHaveText('95%');
  await expect(firstCounted.getByTestId('reason-code')).toHaveText('STUDENT_WROTE_CODE');
  await expect(page.getByTestId('debt-headline')).toHaveText(
    'There is not enough repeated AI delegation here to infer reliance.',
  );
  await shot(page, '03-skill-detail-why');

  // 4. The evidence traces to its source activity.
  await firstCounted.getByTestId('source-link').click();
  await expect(page).toHaveURL(/\/activity\?focus=/);
  await expect(page.getByTestId('activity-focus').getByTestId('activity-row')).toHaveCount(2);
  await shot(page, '04-source-activity');

  // 5. Debt: a reliance signal with its factors, never a score headline.
  await page.goto('/skills');
  await row(x.names.comprehensions).getByRole('link').click();
  await expect(page.getByTestId('debt-panel')).toHaveAttribute('data-band', x.debt_band);
  await expect(page.getByTestId('debt-headline')).toHaveText(x.debt_label);
  await expect(page.getByTestId('debt-factor')).toHaveCount(5);
  await expect(page.getByText('Internal reliance score')).toBeHidden();
  await expect(page.getByText('This is not a judgement of you or of using AI.', { exact: false })).toBeVisible();
  await expect(page.getByTestId('skill-recommendation')).toContainText('Show you can do it on your own');
  await shot(page, '05-debt');

  // 6. Correction on the skill page: Don't count this -> ledger recomputed -> new recommendation.
  await page.goto('/skills');
  await row(x.names.for_loops).getByRole('link').click();
  const target = page.locator('[data-testid="evidence-item"][data-excluded="false"]').first();
  const targetId = await target.getAttribute('data-evidence-id');
  await target.locator('[data-testid="correction-form"][data-action="DONT_COUNT"] [data-testid="correction-start"]').click();
  await target.getByTestId('correction-confirm').click();
  await expect(page.getByTestId('mastery-panel')).toHaveAttribute('data-state', 'DEVELOPING');
  await expect(page.locator(`[data-evidence-id="${targetId}"]`)).toHaveAttribute('data-excluded', 'true');
  await expect(page.getByTestId('skill-recommendation')).toContainText('Build consistency');
  await shot(page, '06-after-dont-count');

  // 7. Correction on the activity page: Wrong skill on a mapped-skill chip.
  await page.goto('/activity');
  const chip = page
    .locator('[data-testid="skill-chip"][data-excluded="false"]')
    .filter({ hasText: x.names.comprehensions })
    .first();
  const mappingId = await chip.getAttribute('data-mapping-id');
  await chip.getByTestId('correction-start').click();
  await chip.getByTestId('correction-confirm').click();
  const corrected = page.locator(`[data-testid="skill-chip"][data-mapping-id="${mappingId}"]`);
  await expect(corrected).toHaveAttribute('data-excluded', 'true');
  await expect(corrected.getByTestId('chip-excluded')).toHaveText('Marked wrong skill');
  await shot(page, '07-after-wrong-skill');

  // 8. Back on the dashboard: counts and recommendations follow the recomputed ledger.
  await page.goto('/dashboard');
  await expectCounts(x.counts_after);
  await expect(page.getByTestId('recommendation')).toHaveCount(x.recommendations_after);
  await expect(page.locator('[data-testid="recommendation"][data-type="PRACTICE"]')).toHaveCount(x.practice_after);
  await shot(page, '08-dashboard-after');
});
