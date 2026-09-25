import { describe, expect, it } from 'vitest';

import { ApiError } from './api';
import { benchmarkLine, budgetLine, failedGates, formKey, retryAction } from './admin';
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
