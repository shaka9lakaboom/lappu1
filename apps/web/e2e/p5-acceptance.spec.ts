/**
 * P5 acceptance walkthrough (manual gate; skipped unless a seeded learner is supplied):
 *
 *   dashboard -> Skill Map -> Skill Detail -> "Why?" -> Activity -> correction
 *   -> recomputed ledger -> updated recommendation
 *
 * The learner is created and seeded by services/backend/scripts/acceptance_p5.py
 * (deterministic fixtures, no model call), which writes P5_ACCEPTANCE_EMAIL and
 * P5_ACCEPTANCE_PASSWORD to a git-ignored file. The web app must point at the same
 * Supabase project and at a backend running without GEMINI_API_KEY.
 * Screenshots go to P5_ACCEPTANCE_OUT (default ../../test-results/p5-acceptance).
 */
import { join } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

const email = process.env.P5_ACCEPTANCE_EMAIL;
const password = process.env.P5_ACCEPTANCE_PASSWORD;
const out = process.env.P5_ACCEPTANCE_OUT ?? join(__dirname, '..', '..', '..', 'test-results', 'p5-acceptance');

test.skip(!email || !password, 'P5 acceptance needs a learner seeded by services/backend/scripts/acceptance_p5.py');

const shot = (page: Page, name: string) => page.screenshot({ path: join(out, `${name}.png`), fullPage: true });

test('P5 student experience: inspect every state, correct evidence, see the recomputed result', async ({ page }) => {
  test.setTimeout(240_000);

  // Sign in as the seeded learner.
  await page.goto('/sign-in?next=/dashboard');
  await page.getByLabel('Email').fill(email!);
  await page.getByLabel('Password').fill(password!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // 1. Dashboard: course card, state counts (UNKNOWN neutral, first), top recommendations.
  const tile = (state: string) => page.locator(`[data-testid="state-count"][data-state="${state}"] dd`);
  await expect(tile('UNKNOWN')).toHaveText('2');
  await expect(tile('DEMONSTRATED')).toHaveText('1');
  await expect(tile('DEVELOPING')).toHaveText('1');
  await expect(tile('EMERGING')).toHaveText('1');
  await expect(tile('VERIFIED')).toHaveText('0');
  await expect(page.locator('[data-testid="state-count"][data-state="UNKNOWN"]')).toContainText('Not enough activity yet');
  await expect(page.getByTestId('dashboard-course-card')).toHaveCount(1);
  const recs = page.getByTestId('recommendation');
  await expect(recs).toHaveCount(3);
  await expect(recs.first()).toHaveAttribute('data-type', 'VERIFY');
  await shot(page, '01-dashboard');

  // 2. Skill map: topic -> skills -> state; a skill without ledger row is UNKNOWN (neutral).
  await page.getByTestId('skill-map-link').click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(page.getByTestId('topic-group')).toHaveCount(2);
  await expect(page.getByTestId('skill-map-row')).toHaveCount(5);
  const whileRow = page.getByTestId('skill-map-row').filter({ hasText: 'Writing while loops' });
  await expect(whileRow).toHaveAttribute('data-state', 'UNKNOWN');
  await expect(whileRow.getByTestId('mastery-badge')).toHaveText('Not enough activity yet');
  await expect(whileRow.getByTestId('mastery-badge')).toHaveAttribute('data-tone', 'neutral');
  await shot(page, '02-skill-map');

  // 3. Skill detail of the demonstrated skill, with the mastery "Why?" and an evidence "Why?".
  await page.getByTestId('skill-map-row').filter({ hasText: 'Writing for loops over sequences' }).getByRole('link').click();
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
  await page.getByTestId('skill-map-row').filter({ hasText: 'Building lists with comprehensions' }).getByRole('link').click();
  await expect(page.getByTestId('debt-panel')).toHaveAttribute('data-band', 'MODERATE');
  await expect(page.getByTestId('debt-headline')).toHaveText('Moderate reliance signal');
  await expect(page.getByTestId('debt-factor')).toHaveCount(5);
  await expect(page.getByText('Internal reliance score')).toBeHidden();
  await expect(page.getByTestId('skill-recommendation')).toContainText('Show you can do it on your own');
  await shot(page, '05-debt');

  // 6. Correction on the skill page: Don't count this -> ledger recomputed -> new recommendation.
  await page.goto('/skills');
  await page.getByTestId('skill-map-row').filter({ hasText: 'Writing for loops over sequences' }).getByRole('link').click();
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
    .filter({ hasText: 'Building lists with comprehensions' })
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
  await expect(tile('DEMONSTRATED')).toHaveText('0');
  await expect(tile('DEVELOPING')).toHaveText('2');
  await expect(page.getByTestId('recommendation')).toHaveCount(4);
  await expect(page.locator('[data-testid="recommendation"][data-type="PRACTICE"]')).toHaveCount(2);
  await shot(page, '08-dashboard-after');
});
