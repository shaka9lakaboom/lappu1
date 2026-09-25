/**
 * Admin view models (P7, ADR 0008). Operational wording only: job states, retry rules, budgets
 * and benchmark verdicts. No captured text or model output ever reaches these pages; error texts
 * arrive redacted from the backend.
 */
import type {
  AdminJob,
  BenchmarkRunSummary,
  JobRetryMode,
  JobState,
  ModelBudgetUsage,
} from '@skillmirror/contracts';

export const JOB_STATE_ORDER: JobState[] = ['PENDING', 'PROCESSING', 'RETRY_WAIT', 'COMPLETED', 'FAILED'];

export const RETRY_LABELS: Record<JobRetryMode, string> = {
  RETRY: 'Retry',
  RESUME_ATTRIBUTION: 'Resume attribution',
};

export const BLOCKED_LABELS: Record<string, string> = {
  RETRY_LIMIT_REACHED: 'Retried 5 times already',
  UNSUPPORTED_JOB_TYPE: 'No manual retry for this job type',
  VERIFICATION_CLOSED: 'Verification closed with its failure recorded; the planner plans a new one',
};

export function retryAction(job: Pick<AdminJob, 'retry_mode' | 'retry_blocked'>):
  | { kind: 'action'; mode: JobRetryMode; label: string }
  | { kind: 'blocked'; label: string }
  | { kind: 'none' } {
  if (job.retry_mode) return { kind: 'action', mode: job.retry_mode, label: RETRY_LABELS[job.retry_mode] };
  if (job.retry_blocked) return { kind: 'blocked', label: BLOCKED_LABELS[job.retry_blocked] ?? job.retry_blocked };
  return { kind: 'none' };
}

export function budgetLine(budget: ModelBudgetUsage): string {
  if (budget.limit === null) return `${budget.requests} requests today (no daily limit configured)`;
  return `${budget.requests} of ${budget.limit} today · reserve ${budget.reserve} · ${budget.available ?? 0} available`;
}

export function benchmarkLine(run: Pick<BenchmarkRunSummary, 'passed_count' | 'case_count' | 'failed_count' | 'blocked_count'>): string {
  const extra = [
    run.failed_count ? `${run.failed_count} failed` : null,
    run.blocked_count ? `${run.blocked_count} blocked` : null,
  ].filter(Boolean);
  return `${run.passed_count}/${run.case_count} passed${extra.length ? ` (${extra.join(', ')})` : ''}`;
}

/** Hard gates that did not pass, by name. */
export function failedGates(run: Pick<BenchmarkRunSummary, 'hard_gates'>): string[] {
  return Object.entries(run.hard_gates)
    .filter(([, gate]) => typeof gate === 'object' && gate !== null && (gate as { pass?: unknown }).pass === false)
    .map(([name]) => name)
    .sort();
}

/** A fresh Idempotency-Key for one rendered form: a double submit replays, never repeats. */
export function formKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}
