/**
 * P9 end-to-end run on the replay provider (H13; CI job "E2E replay"; 0 real Gemini calls).
 *
 *   sign in -> the course exists -> three ChatGPT turns captured (sent exactly as the extension
 *   sends them) -> the real worker processes each -> Activity shows the mapped evidence -> Skill
 *   Detail explains the state -> the fixture warrants a VERIFY -> the Verification Center plans a
 *   check, the worker prepares it, the learner answers it -> deterministic grade -> VERIFICATION
 *   evidence -> the ledger and the recommendation update.
 *
 * Needs `services/backend/scripts/e2e_replay.py prepare` + `serve` against the local Supabase
 * stack and the web app built against that stack; E2E_REPLAY_STATE names the prepared state.
 */
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { createClient } from '@supabase/supabase-js';
import { expect, test, type Page } from '@playwright/test';

interface Turn {
  user: string;
  assistant: string;
  user_hash: string;
  assistant_hash: string;
}

interface State {
  email: string;
  password: string;
  learner_id: string;
  course_id: string;
  course_name: string;
  skill_id: string;
  turns: Turn[];
  correct_choice: string;
  api: string;
}

const statePath = process.env.E2E_REPLAY_STATE;

function envelope(state: State, fields: Record<string, unknown>) {
  const external = String(fields.external_message_id);
  return {
    event_id: randomUUID(),
    schema_version: 1,
    learner_id: state.learner_id,
    source_provider: 'chatgpt',
    source_method: 'browser_extension',
    external_parent_message_id: null,
    content_format: 'text',
    occurred_at: null,
    provider_model: null,
    revision_index: 0,
    attachment_metadata: [],
    context_incomplete: false,
    active_course_id: null,
    client_event_id: `chatgpt:${external}:r0`,
    ...fields,
  };
}

async function activity(api: string, token: string) {
  const response = await fetch(`${api}/v1/activity?limit=50`, { headers: { Authorization: `Bearer ${token}` } });
  expect(response.status).toBe(200);
  return (await response.json()) as { items: { id: string; processing_state: string | null; processing_outcome: string | null }[] };
}

async function signIn(page: Page, state: State) {
  await page.goto('/sign-in');
  await page.getByLabel('Email').fill(state.email);
  await page.getByLabel('Password').fill(state.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test('replay E2E: capture -> worker -> Activity -> Skill Detail -> verification -> ledger', async ({ page }) => {
  test.skip(!statePath, 'needs services/backend/scripts/e2e_replay.py prepare (E2E_REPLAY_STATE)');
  test.setTimeout(300_000);
  const state = JSON.parse(readFileSync(statePath!, 'utf8')) as State;

  // 1. Sign in through the real UI; 2. the course exists.
  await signIn(page, state);
  await expect(page.getByTestId('dashboard-course-card')).toContainText(state.course_name);
  await expect(page.getByTestId('attention-empty')).toBeVisible(); // nothing to check yet

  // 3. Capture: the extension's envelopes, one turn at a time, each processed before the next.
  const supabase = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data, error } = await supabase.auth.signInWithPassword({ email: state.email, password: state.password });
  expect(error).toBeNull();
  const token = data.session!.access_token;
  const conversation = `e2e-${randomUUID()}`;
  for (const [index, turn] of state.turns.entries()) {
    const capturedAt = new Date(Date.now() - 5 * 60_000 * (state.turns.length - 1 - index)).toISOString();
    const userId = `u-${randomUUID().slice(0, 12)}`;
    const events = [
      envelope(state, {
        external_conversation_id: conversation,
        external_message_id: userId,
        message_index: index * 2,
        role: 'user',
        content_text: turn.user,
        content_hash: turn.user_hash,
        captured_at: capturedAt,
      }),
      envelope(state, {
        external_conversation_id: conversation,
        external_message_id: `a-${randomUUID().slice(0, 12)}`,
        external_parent_message_id: userId,
        message_index: index * 2 + 1,
        role: 'assistant',
        content_text: turn.assistant,
        content_hash: turn.assistant_hash,
        captured_at: capturedAt,
      }),
    ];
    const response = await fetch(`${state.api}/v1/events/batch`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ client: { extension_version: '0.2.0', adapter_version: 'chatgpt-2' }, events }),
    });
    expect(response.status, await response.clone().text()).toBeLessThan(300);
    // 4. The real worker processes the turn (replayed model output).
    await expect
      .poll(async () => (await activity(state.api, token)).items.filter((i) => i.processing_state === 'COMPLETED').length, {
        timeout: 60_000,
        intervals: [500, 1000],
      })
      .toBe((index + 1) * 2);
  }
  const outcomes = (await activity(state.api, token)).items.map((i) => i.processing_outcome);
  expect(outcomes.filter((o) => o === 'EVIDENCE_RECORDED')).toHaveLength(3);

  // 5. Activity shows the mapped evidence, worded by the recorded evidence.
  await page.goto('/activity');
  const chips = page.locator(`[data-testid="skill-chip"]`).filter({ hasText: 'Aggregating rows with GROUP BY' });
  await expect(chips.first()).toBeVisible();
  await expect(chips.first().getByTestId('chip-who')).toHaveText('Demonstrated by: AI');
  await expect(chips.first().getByTestId('chip-effect')).toHaveText('AI-assistance evidence: no mastery credit');
  await expect(page.getByTestId('activity-outcome').first()).toContainText('no mastery credit');

  // 6. Skill Detail explains the state: UNKNOWN (neutral), a reliance signal, a check suggested.
  await page.goto(`/skills/${state.skill_id}`);
  await expect(page.getByTestId('mastery-panel')).toHaveAttribute('data-state', 'UNKNOWN');
  await page.getByTestId('mastery-why').locator('summary').click();
  await expect(page.getByTestId('mastery-why')).toContainText('the AI doing the work never counts as yours');
  await expect(page.getByTestId('debt-panel')).not.toHaveAttribute('data-band', 'NONE');
  await expect(page.getByTestId('evidence-item').first()).toBeVisible();
  // 7. The fixture warrants a VERIFY (repeated delegation): the recommendation offers the check.
  await expect(page.getByTestId('recommendation-verify')).toBeVisible();
  await page.goto('/dashboard');
  await expect(page.getByTestId('dashboard-attention-list')).toContainText('Aggregating rows with GROUP BY');

  // 8. Verification: the centre plans it (no model call), the worker prepares it.
  await page.goto('/verifications');
  const ready = page.getByTestId('verifications-ready').getByTestId('verification-open');
  await expect(ready).toBeVisible({ timeout: 60_000 }); // the page refreshes while preparing
  await ready.click();
  await expect(page).toHaveURL(/\/verifications\/[0-9a-f-]{36}$/);
  await page.getByTestId('verification-start').click();
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'IN_PROGRESS');
  await expect(page.getByTestId('challenge-prompt')).toBeVisible();
  await page.getByTestId('challenge-choice').filter({ hasText: state.correct_choice }).click();
  await page.getByTestId('challenge-submit').click();
  // 9. Deterministic grade -> result + VERIFICATION evidence (the worker grades).
  await expect(page.getByTestId('verification-page')).toHaveAttribute('data-state', 'EVALUATED', { timeout: 60_000 });
  await expect(page.getByTestId('verification-result')).toHaveAttribute('data-passed', 'true');
  await expect(page.getByTestId('verification-result')).toContainText('Checked automatically against the answer key.');

  // 10. The ledger is re-derived from the VERIFICATION evidence; the VERIFY is resolved.
  await page.goto(`/skills/${state.skill_id}`);
  await expect(page.getByTestId('evidence-item').filter({ hasText: 'SkillMirror check' })).toHaveCount(1);
  await expect(page.getByTestId('recommendation-verify')).toHaveCount(0);
  const ledger = await fetch(`${state.api}/v1/ledger?course_id=${state.course_id}`, { headers: { Authorization: `Bearer ${token}` } });
  const entry = ((await ledger.json()) as { skills: { skill_id: string; debt_actionable: boolean; evidence_count: number }[] }).skills.find(
    (s) => s.skill_id === state.skill_id,
  );
  expect(entry?.debt_actionable).toBe(false);
  expect(entry?.evidence_count).toBeGreaterThanOrEqual(4);
});
