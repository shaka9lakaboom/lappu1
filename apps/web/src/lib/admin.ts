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

export interface BenchmarkBreakdown {
  gatesOk: boolean;
  /** Safety / correctness hard gates. */
  gates: string;
  /** Case completion. */
  completion: string;
  /** Provider / transport failures. */
  provider: string;
  /** Why the stored verdict reads as it does (null when nothing needs explaining). */
  note: string | null;
  /** Where the failing cases' error codes come from, when not the run's own report. */
  source: string | null;
}

/**
 * P9: the parts of a stored verdict, shown next to it - never instead of it. A LIVE run that
 * held every hard gate but lost one case to a provider transport error stays FAIL as recorded;
 * the breakdown says why.
 */
export function benchmarkBreakdown(
  run: Pick<
    BenchmarkRunSummary,
    | 'mode'
    | 'verdict'
    | 'case_count'
    | 'passed_count'
    | 'blocked_count'
    | 'hard_gates_total'
    | 'hard_gates_failed'
    | 'failed_without_hard_gate'
    | 'failing_cases'
    | 'provider_failure_cases'
    | 'case_errors_source'
  >,
): BenchmarkBreakdown {
  const gatesOk = run.hard_gates_failed.length === 0;
  const gates = gatesOk
    ? `PASS · all ${run.hard_gates_total} hard gates held`
    : `FAIL · ${run.hard_gates_failed.length} of ${run.hard_gates_total} failed: ${run.hard_gates_failed.join(', ')}`;
  const completion =
    `${run.passed_count} / ${run.case_count} cases passed` + (run.blocked_count ? ` · ${run.blocked_count} blocked` : '');
  const cases = run.provider_failure_cases;
  const provider =
    cases === null
      ? run.failed_without_hard_gate > 0
        ? 'not recorded for this run'
        : '0'
      : cases.length > 0
        ? `${cases.length} (${cases.join(', ')})`
        : '0';
  let note: string | null = null;
  if (run.verdict === 'FAIL' && gatesOk && run.failed_without_hard_gate > 0) {
    note =
      cases !== null && cases.length === run.failed_without_hard_gate
        ? `Every safety and correctness hard gate held. The stored verdict stays FAIL because a ${run.mode.toLowerCase()} run passes only when every case completes, and ${cases.join(', ')} ${cases.length === 1 ? 'was' : 'were'} stopped by a provider / transport error.`
        : `Every safety and correctness hard gate held. The stored verdict stays FAIL: ${run.failing_cases.join(', ') || `${run.failed_without_hard_gate} case(s)`} did not pass without breaking a hard gate.`;
  } else if (run.verdict === 'FAIL' && !gatesOk) {
    note = 'A zero-tolerance hard gate failed: this run is a real FAIL.';
  }
  const source = run.case_errors_source && run.case_errors_source !== 'run report' ? run.case_errors_source : null;
  return { gatesOk, gates, completion, provider, note, source };
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
