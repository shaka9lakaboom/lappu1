/**
 * Hosted E2E smoke walkthrough of the merged P7 + P8 code (manual gate; skipped unless accounts are
 * supplied by services/backend/scripts/smoke_p8_hosted.py):
 *
 *   learner -> /activity: every skill chip shows the recorded evidence's actor and type (ADR 0008
 *              §24) -> the skill page (no VERIFY) -> /verifications (nothing planned)
 *   admin   -> /admin/benchmark: the recorded deterministic / replay / live runs, latest per mode
 *
 * Accounts and expectations come from git-ignored files: P8_LEARNER_EMAIL / _PASSWORD +
 * P8_SMOKE_EXPECT (verify phase), P8_ADMIN_EMAIL / _PASSWORD + P8_BENCHMARK_EXPECT (benchmark
 * phase). Screenshots go to P8_SMOKE_OUT.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect as baseExpect, test, type Page } from '@playwright/test';

const expect = baseExpect.configure({ timeout: 30_000 });

interface Chip {
  message_id: string;
  mapping_id: string;
  skill_id: string;
  actor: 'STUDENT' | 'AI' | 'SHARED' | 'UNKNOWN';
  evidence_type: string;
  qualification_reason: string;
}

const env = (name: string) => process.env[name];
const out = env('P8_SMOKE_OUT') ?? join(__dirname, '..', '..', '..', 'test-results', 'p8-hosted');
// P9: the chip's "who" follows the recorded evidence type (never the attributor's claim).
const WHO: Record<string, string> = {
  EXPOSURE: 'Explanation seen',
  OBSERVATION: 'Demonstrated by: AI',
  ASSISTED_ATTEMPT: 'Demonstrated by: You + AI',
  INDEPENDENT_EXPLANATION: 'Demonstrated by: You',
  INDEPENDENT_APPLICATION: 'Demonstrated by: You',
  TRANSFER: 'Demonstrated by: You',
};
const TYPE: Record<string, string> = {
  EXPOSURE: 'Saw an explanation',
  OBSERVATION: 'The AI did it',
  ASSISTED_ATTEMPT: 'Assisted attempt',
  INDEPENDENT_EXPLANATION: 'Explained it yourself',
  INDEPENDENT_APPLICATION: 'Applied it yourself',
  TRANSFER: 'Applied it in a new context',
};

const shot = async (page: Page, name: string) => {
  await expect(page.getByTestId('page-loading')).toHaveCount(0);
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true });
};

async function signIn(page: Page, who: 'LEARNER' | 'ADMIN', next = '/dashboard') {
  await page.goto(`/sign-in?next=${encodeURIComponent(next)}`);
  await page.getByLabel('Email').fill(env(`P8_${who}_EMAIL`)!);
  await page.getByLabel('Password').fill(env(`P8_${who}_PASSWORD`)!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[/?]/g, '\\$&')}$`));
}

test.describe.configure({ mode: 'serial' });

test('learner: activity chips equal the recorded evidence; no VERIFY, nothing planned', async ({ page }) => {
  test.skip(!env('P8_LEARNER_EMAIL') || !env('P8_SMOKE_EXPECT'), 'needs smoke_p8_hosted.py verify');
  test.setTimeout(180_000);
  const e = JSON.parse(readFileSync(env('P8_SMOKE_EXPECT')!, 'utf-8')) as { chips: Chip[]; skills: string[] };
  await signIn(page, 'LEARNER');
  await shot(page, '01-dashboard');

  await page.goto('/activity');
  await expect(page.getByTestId('activity-row').first()).toBeVisible();
  for (const chip of e.chips) {
    const row = page.locator(`[data-testid="skill-chip"][data-mapping-id="${chip.mapping_id}"]`);
    await expect(row.getByTestId('chip-who')).toHaveText(WHO[chip.evidence_type]);
    await expect(row.getByTestId('chip-evidence-type')).toContainText(TYPE[chip.evidence_type]);
    await expect(row.getByTestId('chip-qualification')).toHaveCount(chip.qualification_reason === 'COPIED_FROM_AI' ? 1 : 0);
  }
  await shot(page, '02-activity');

  for (const [i, skill] of e.skills.entries()) {
    await page.goto(`/skills/${skill}`);
    await expect(page.getByTestId('mastery-panel')).toBeVisible();
    await expect(page.getByTestId('debt-panel')).toBeVisible();
    await expect(page.getByTestId('recommendation-verify')).toHaveCount(0);
    await shot(page, `03-skill-${i + 1}`);
  }

  await page.goto('/verifications');
  await expect(page.getByTestId('verifications-empty')).toBeVisible();
  await shot(page, '04-verifications');
});

test('admin: /admin/benchmark shows the recorded runs', async ({ page }) => {
  test.skip(!env('P8_ADMIN_EMAIL') || !env('P8_BENCHMARK_EXPECT'), 'needs smoke_p8_hosted.py benchmark');
  test.setTimeout(120_000);
  await signIn(page, 'ADMIN');
  await page.goto('/admin/benchmark');
  const card = (mode: string) => page.locator(`[data-testid="benchmark-latest"][data-mode="${mode}"]`);
  await expect(card('DETERMINISTIC')).toHaveAttribute('data-verdict', 'PASS');
  await expect(card('DETERMINISTIC')).toContainText('120/120 passed');
  await expect(card('DETERMINISTIC')).toContainText('scripted · 0 requests');
  await expect(card('REPLAY')).toHaveAttribute('data-verdict', 'PASS');
  await expect(card('REPLAY')).toContainText('72/72 passed');
  await expect(card('REPLAY')).toContainText('gemini-3.5-flash-lite · 0 requests');
  await expect(card('LIVE')).toHaveAttribute('data-verdict', 'FAIL');
  await expect(card('LIVE')).toContainText('71/72 passed (1 failed)');
  await expect(card('LIVE')).toContainText('gemini-3.5-flash-lite · 183 requests');
  for (const mode of ['DETERMINISTIC', 'REPLAY', 'LIVE']) {
    await expect(card(mode).getByTestId('benchmark-gates')).toHaveText('PASS · all 13 hard gates held');
  }
  // P9: the parts of LIVE's stored FAIL, shown next to it (never rewritten).
  await expect(card('LIVE').getByTestId('benchmark-completion')).toHaveText('71 / 72 cases passed');
  await expect(card('LIVE').getByTestId('benchmark-provider')).toHaveText('1 (REL-06)');
  await expect(card('LIVE').getByTestId('benchmark-verdict')).toHaveText('FAIL');
  await expect(page.getByTestId('benchmark-runs').locator('tbody tr')).toHaveCount(3);
  await shot(page, '05-admin-benchmark');
});
