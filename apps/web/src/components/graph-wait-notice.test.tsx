import type { Course } from '@skillmirror/contracts';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { GraphWaitNotice } from '@/components/graph-wait-notice';
import { utcTimeLabel } from '@/components/local-time';

const course: Course = {
  id: 'de89f3c1-8c49-47c8-9858-4fe1f841a603',
  name: 'Introduction to Python',
  subject: null,
  level: null,
  description: null,
  status: 'ACTIVE',
  graph_status: 'GENERATING',
  graph_version: 0,
  graph_error: null,
  graph_generated_at: null,
  role: 'STUDENT',
  is_owner: true,
  skill_count: 0,
  bootstrap_job_state: 'PENDING',
  bootstrap_wait_reason: 'MODEL_BACKPRESSURE',
  bootstrap_next_attempt_at: '2026-09-25T14:55:00.000Z',
  created_at: '2026-09-25T14:42:17Z',
  updated_at: '2026-09-25T14:54:00Z',
};
const before = new Date('2026-09-25T14:50:00Z');

describe('GraphWaitNotice', () => {
  it('renders the provider wait and the automatic retry time (UTC on the server)', () => {
    const html = renderToStaticMarkup(<GraphWaitNotice course={course} now={before} />);
    expect(html).toContain('data-reason="MODEL_BACKPRESSURE"');
    expect(html).toContain('Skill graph generation is temporarily waiting for the AI provider.');
    expect(html).toContain('Retrying automatically around <time dateTime="2026-09-25T14:55:00.000Z"');
    expect(html).toContain('14:55 UTC');
    expect(html).not.toContain('graph-wait-overdue');
  });

  it('says so when the retry is overdue because no worker is running', () => {
    const html = renderToStaticMarkup(<GraphWaitNotice course={course} now={new Date('2026-09-25T15:10:00Z')} />);
    expect(html).toContain('data-testid="graph-wait-overdue"');
    expect(html).toContain('no SkillMirror worker has picked it up yet');
    expect(html).not.toContain('graph-wait-retry');
  });

  it('renders nothing for a running, ready or failed graph', () => {
    for (const change of [
      { bootstrap_job_state: 'PROCESSING', bootstrap_wait_reason: null, bootstrap_next_attempt_at: null },
      { graph_status: 'READY', bootstrap_job_state: 'COMPLETED', bootstrap_next_attempt_at: null },
      { graph_status: 'FAILED' },
    ] as const) {
      expect(renderToStaticMarkup(<GraphWaitNotice course={{ ...course, ...change }} now={before} />)).toBe('');
    }
  });

  it('formats the server fallback time as UTC', () => {
    expect(utcTimeLabel('2026-09-25T15:56:11.467623Z')).toBe('15:56 UTC');
  });
});
