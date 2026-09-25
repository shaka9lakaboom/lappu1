/**
 * P7 acceptance walkthrough (manual gate; skipped unless accounts are supplied):
 *
 *   teacher  -> /teacher -> class overview (cohort banner, state bar with "Not enough evidence yet",
 *               per-skill counts, no student data) -> small group (suppressed)
 *   student  -> /admin and /teacher show the forbidden panel; no area links on the dashboard
 *   admin    -> dashboard area links -> /admin tiles -> retry a failed job -> reject a candidate
 *               -> model runs -> benchmark
 *
 * Accounts and expectations are created by services/backend/scripts/acceptance_p7.py (prepare)
 * and read from git-ignored files: P7_TEACHER_EMAIL / _PASSWORD, P7_ADMIN_*, P7_STUDENT_*,
 * P7_ACCEPTANCE_EXPECT. Screenshots go to P7_ACCEPTANCE_OUT. The script's verify phase then
 * checks what the browser did (one retry, one reject, both audited).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect as baseExpect, test, type Page } from '@playwright/test';

const expect = baseExpect.configure({ timeout: 30_000 });

interface Expectations {
  course_id: string;
  small_course_id: string;
  job_id: string;
  candidate_id: string;
  skills: number;
  students: number;
}

const env = (name: string) => process.env[name];
const expectFile = env('P7_ACCEPTANCE_EXPECT');
const out = env('P7_ACCEPTANCE_OUT') ?? join(__dirname, '..', '..', '..', 'test-results', 'p7-local');

test.skip(
  !env('P7_TEACHER_EMAIL') || !env('P7_ADMIN_EMAIL') || !env('P7_STUDENT_EMAIL') || !expectFile,
  'P7 acceptance needs accounts prepared by services/backend/scripts/acceptance_p7.py',
);
test.describe.configure({ mode: 'serial' });

const shot = async (page: Page, name: string) => {
  await expect(page.getByTestId('page-loading')).toHaveCount(0);
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true });
};

async function signIn(page: Page, who: 'TEACHER' | 'ADMIN' | 'STUDENT', next = '/dashboard') {
  await page.goto(`/sign-in?next=${encodeURIComponent(next)}`);
  await page.getByLabel('Email').fill(env(`P7_${who}_EMAIL`)!);
  await page.getByLabel('Password').fill(env(`P7_${who}_PASSWORD`)!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[/?]/g, '\\$&')}$`));
}

const x = () => JSON.parse(readFileSync(expectFile!, 'utf-8')) as Expectations;

test('teacher: class overview without individual data, small group suppressed', async ({ page }) => {
  test.setTimeout(180_000);
  const e = x();
  await signIn(page, 'TEACHER', '/teacher');
  await expect(page.getByTestId('teacher-course-card')).toHaveCount(2);
  await expect(page.locator(`[data-course-id="${e.small_course_id}"]`).getByTestId('teacher-course-suppressed')).toBeVisible();
  await shot(page, '01-teacher-courses');

  await page.locator(`[data-course-id="${e.course_id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/teacher/courses/${e.course_id}$`));
  await expect(page.getByTestId('cohort-banner')).toHaveAttribute('data-suppressed', 'false');
  await expect(page.getByTestId('cohort-banner')).toContainText(`${e.students} students enrolled`);
  await expect(page.getByTestId('state-legend')).toContainText('Not enough evidence yet: 6');
  await expect(page.getByTestId('teacher-skill-row')).toHaveCount(e.skills);
  await expect(page.getByTestId('verification-needs')).toContainText('3 students');
  await expect(page.getByTestId('evidence-independent')).toContainText('27');
  // No student identity reaches the page.
  await expect(page.locator('body')).not.toContainText('@mailinator.com');
  await expect(page.locator('body')).not.toContainText('@example.test');
  await shot(page, '02-teacher-overview');

  await page.goto(`/teacher/courses/${e.small_course_id}`);
  await expect(page.getByTestId('cohort-banner')).toHaveAttribute('data-suppressed', 'true');
  await expect(page.getByTestId('overview-suppressed')).toBeVisible();
  await expect(page.getByTestId('teacher-skill-table')).toHaveCount(0);
  await shot(page, '03-teacher-small-group');
});

test('student: the teacher and admin areas are refused', async ({ page }) => {
  await signIn(page, 'STUDENT');
  await expect(page.getByTestId('area-link')).toHaveCount(0);
  await page.goto('/admin');
  await expect(page.getByTestId('forbidden-panel')).toContainText('administrators');
  await shot(page, '04-student-admin-forbidden');
  await page.goto('/teacher');
  await expect(page.getByTestId('forbidden-panel')).toContainText('teachers');
});

test('admin: overview, retry a failed job, reject a candidate, model runs, benchmark', async ({ page }) => {
  test.setTimeout(180_000);
  const e = x();
  await signIn(page, 'ADMIN');
  await expect(page.getByTestId('area-link')).toHaveCount(2);
  await page.goto('/admin');
  await expect(page.getByTestId('tile-failed-jobs')).toBeVisible();
  await expect(page.getByTestId('budget')).toBeVisible();
  await shot(page, '05-admin-overview');

  await page.goto('/admin/jobs?state=FAILED');
  const row = page.locator(`[data-job-id="${e.job_id}"]`);
  await expect(row).toHaveAttribute('data-state', 'FAILED');
  await expect(row).not.toContainText('AIza');
  await expect(row).toContainText('[redacted-api-key]');
  await shot(page, '06-admin-jobs-failed');
  await row.getByRole('button', { name: 'Retry' }).click();
  // The refreshed FAILED list no longer holds it: it is queued again.
  await expect(page.locator(`[data-job-id="${e.job_id}"]`)).toHaveCount(0);
  await page.goto('/admin/jobs?state=PENDING');
  await expect(page.locator(`[data-job-id="${e.job_id}"]`)).toHaveAttribute('data-state', 'PENDING');
  await shot(page, '07-admin-job-retried');

  await page.goto('/admin/skill-candidates');
  const card = page.locator(`[data-candidate-id="${e.candidate_id}"]`);
  await expect(card).toHaveAttribute('data-status', 'PENDING_REVIEW');
  await card.getByLabel('Note').fill('Synthetic acceptance candidate.');
  await card.getByRole('button', { name: 'Reject' }).click();
  // The refreshed review queue no longer holds it; the REJECTED list does.
  await expect(page.locator(`[data-candidate-id="${e.candidate_id}"]`)).toHaveCount(0);
  await page.goto('/admin/skill-candidates?status=REJECTED');
  await expect(page.locator(`[data-candidate-id="${e.candidate_id}"]`)).toHaveAttribute('data-status', 'REJECTED');
  await shot(page, '08-admin-candidate-rejected');

  await page.goto('/admin/model-runs');
  await expect(page.getByTestId('status-counts')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('"output"');
  await shot(page, '09-admin-model-runs');

  await page.goto('/admin/benchmark');
  await expect(page.getByTestId('benchmark-empty').or(page.getByTestId('benchmark-runs'))).toBeVisible();
  await shot(page, '10-admin-benchmark');
});
