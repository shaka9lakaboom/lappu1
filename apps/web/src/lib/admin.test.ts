import { describe, expect, it } from 'vitest';

import { ApiError } from './api';
import { benchmarkBreakdown, benchmarkLine, budgetLine, failedGates, formKey, retryAction } from './admin';
import { areaLinks, loadArea } from './roles';

describe('retry actions', () => {
  it('offers the mode the backend allows, or says why not', () => {
    expect(retryAction({ retry_mode: 'RETRY', retry_blocked: null })).toEqual({ kind: 'action', mode: 'RETRY', label: 'Retry' });
    expect(retryAction({ retry_mode: 'RESUME_ATTRIBUTION', retry_blocked: null })).toMatchObject({
      kind: 'action',
      label: 'Resume attribution',
    });
    expect(retryAction({ retry_mode: null, retry_blocked: 'RETRY_LIMIT_REACHED' })).toEqual({
      kind: 'blocked',
      label: 'Retried 5 times already',
    });
    expect(retryAction({ retry_mode: null, retry_blocked: 'SOMETHING_NEW' })).toEqual({ kind: 'blocked', label: 'SOMETHING_NEW' });
    expect(retryAction({ retry_mode: null, retry_blocked: null })).toEqual({ kind: 'none' });
  });

  it('mints a fresh idempotency key per rendered form', () => {
    const a = formKey('retry');
    expect(a).toMatch(/^retry-[0-9a-f-]{36}$/);
    expect(formKey('retry')).not.toBe(a);
  });
});

describe('budget and benchmark lines', () => {
  it('shows used / limit / reserve / available', () => {
    const line = budgetLine({
      provider: 'google',
      model: 'gemini-3.5-flash-lite',
      requests: 6,
      limit: 500,
      reserve: 25,
      available: 469,
      resets_at: '2026-09-26T07:00:00Z',
    });
    expect(line).toBe('6 of 500 today · reserve 25 · 469 available');
    expect(
      budgetLine({ provider: 'google', model: 'x', requests: 2, limit: null, reserve: 25, available: null, resets_at: '' }),
    ).toContain('no daily limit');
  });

  it('summarises a run and names failed hard gates', () => {
    expect(benchmarkLine({ passed_count: 120, case_count: 120, failed_count: 0, blocked_count: 0 })).toBe('120/120 passed');
    expect(benchmarkLine({ passed_count: 60, case_count: 72, failed_count: 10, blocked_count: 2 })).toBe(
      '60/72 passed (10 failed, 2 blocked)',
    );
    expect(
      failedGates({
        hard_gates: { false_debt: { value: 0, pass: true }, injection: { value: 1, pass: false }, x: 'bad' },
      }),
    ).toEqual(['injection']);
  });
});

describe('benchmark breakdown (P9: the parts of a stored verdict, never a rewrite)', () => {
  // The hosted LIVE row: 71/72, every hard gate held, REL-06 stopped by a provider transport error.
  const live = {
    mode: 'LIVE' as const,
    verdict: 'FAIL' as const,
    case_count: 72,
    passed_count: 71,
    blocked_count: 0,
    hard_gates_total: 13,
    hard_gates_failed: [],
    failed_without_hard_gate: 1,
    failing_cases: ['REL-06'],
    provider_failure_cases: ['REL-06'],
    case_errors_source: 'saved runner report live_full.json, sha256 90e600d6… (ADR 0008 §35)',
  };

  it('separates hard gates PASS, completion 71 / 72 and one transport failure from the stored FAIL', () => {
    const parts = benchmarkBreakdown(live);
    expect(parts.gatesOk).toBe(true);
    expect(parts.gates).toBe('PASS · all 13 hard gates held');
    expect(parts.completion).toBe('71 / 72 cases passed');
    expect(parts.provider).toBe('1 (REL-06)');
    expect(parts.note).toContain('Every safety and correctness hard gate held');
    expect(parts.note).toContain('stays FAIL');
    expect(parts.note).toContain('REL-06 was stopped by a provider / transport error');
    expect(parts.source).toContain('sha256');
  });

  it('never softens a real hard-gate failure', () => {
    const parts = benchmarkBreakdown({ ...live, hard_gates_failed: ['false_ai_assistance_debt'], failed_without_hard_gate: 0, provider_failure_cases: [] });
    expect(parts.gatesOk).toBe(false);
    expect(parts.gates).toBe('FAIL · 1 of 13 failed: false_ai_assistance_debt');
    expect(parts.note).toBe('A zero-tolerance hard gate failed: this run is a real FAIL.');
  });

  it('says so when the cause of an incomplete case was not recorded', () => {
    const parts = benchmarkBreakdown({ ...live, provider_failure_cases: null, case_errors_source: null });
    expect(parts.provider).toBe('not recorded for this run');
    expect(parts.note).toContain('REL-06 did not pass without breaking a hard gate');
    expect(parts.source).toBeNull();
  });

  it('shows a clean run plainly', () => {
    const parts = benchmarkBreakdown({
      ...live,
      mode: 'REPLAY',
      verdict: 'PASS',
      passed_count: 72,
      failed_without_hard_gate: 0,
      failing_cases: [],
      provider_failure_cases: [],
      case_errors_source: 'run report',
    });
    expect([parts.gates, parts.completion, parts.provider, parts.note, parts.source]).toEqual([
      'PASS · all 13 hard gates held',
      '72 / 72 cases passed',
      '0',
      null,
      null,
    ]);
  });
});

describe('areas and role-aware loading', () => {
  it('shows only the areas the profile role opens', () => {
    expect(areaLinks(null)).toEqual([]);
    expect(areaLinks({ capabilities: { student: true, teacher: false, admin: false } })).toEqual([]);
    expect(areaLinks({ capabilities: { student: true, teacher: true, admin: false } }).map((l) => l.href)).toEqual(['/teacher']);
    expect(areaLinks({ capabilities: { student: true, teacher: true, admin: true } }).map((l) => l.href)).toEqual([
      '/teacher',
      '/admin',
    ]);
  });

  it('turns 403 and 404 into states and keeps other errors as messages', async () => {
    const failing = (status: number) =>
      (async () => {
        throw new ApiError(status, `HTTP ${status}`);
      }) as never;
    expect(await loadArea('/v1/admin/overview', 't', failing(403))).toEqual({ state: 'forbidden', message: 'HTTP 403' });
    expect(await loadArea('/v1/admin/overview', 't', failing(404))).toEqual({ state: 'not_found' });
    expect(await loadArea('/v1/admin/overview', 't', failing(503))).toEqual({ state: 'error', message: 'HTTP 503' });
    const ok = (async () => ({ fine: true })) as never;
    expect(await loadArea('/v1/admin/overview', 't', ok)).toEqual({ state: 'ok', data: { fine: true } });
  });
});
