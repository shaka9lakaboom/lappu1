import type { CourseSkill, CourseSkillsResponse, SkillNode } from '@skillmirror/contracts';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, apiBaseUrl, apiRequest } from './api';
import {
  buildCourseRequest,
  graphStatusLabel,
  graphWait,
  groupSkillsByTopic,
  importanceLabel,
  isGraphInProgress,
  prerequisiteNames,
} from './courses';

function node(id: string, name: string, kind: SkillNode['node_kind'] = 'SKILL'): SkillNode {
  return {
    id,
    slug: id,
    canonical_name: name,
    description: `${name} description`,
    node_kind: kind,
    status: 'ACTIVE',
    version: 1,
    difficulty_band: 2,
    assessment_types: ['short_response'],
    aliases: [],
  };
}

function entry(skill: SkillNode, importance = 0.5): CourseSkill {
  return { skill, importance, source: 'COURSE_BOOTSTRAP', active: true, graph_version: 1, embedded: true };
}

const graph: CourseSkillsResponse = {
  course_id: 'c1',
  graph_status: 'READY',
  graph_version: 1,
  skills: [
    entry(node('t-loops', 'Loops', 'TOPIC')),
    entry(node('t-data', 'Data Structures', 'TOPIC')),
    entry(node('s-for', 'For Loops'), 0.9),
    entry(node('s-while', 'While Loops'), 0.5),
    entry(node('s-dict', 'Dictionaries'), 0.5),
    entry(node('s-orphan', 'Orphan Skill')),
  ],
  edges: [
    { from_skill_id: 't-loops', to_skill_id: 's-while', edge_type: 'PARENT', weight: 1 },
    { from_skill_id: 't-loops', to_skill_id: 's-for', edge_type: 'PARENT', weight: 1 },
    { from_skill_id: 't-data', to_skill_id: 's-dict', edge_type: 'PARENT', weight: 1 },
    { from_skill_id: 's-for', to_skill_id: 's-while', edge_type: 'PREREQUISITE', weight: 1 },
  ],
};

describe('course graph helpers', () => {
  it('groups assessable skills under topics, most important first, orphans last', () => {
    const groups = groupSkillsByTopic(graph);
    expect(groups.map((g) => g.topic?.canonical_name ?? null)).toEqual(['Data Structures', 'Loops', null]);
    expect(groups[1].skills.map((s) => s.skill.canonical_name)).toEqual(['For Loops', 'While Loops']);
    expect(groups[2].skills.map((s) => s.skill.id)).toEqual(['s-orphan']);
  });

  it('lists prerequisites by name', () => {
    expect(prerequisiteNames(graph).get('s-while')).toEqual(['For Loops']);
  });

  it('labels bootstrap status and importance', () => {
    expect(graphStatusLabel({ graph_status: 'PENDING', bootstrap_job_state: 'PENDING' })).toBe(
      'Queued for skill graph generation',
    );
    expect(graphStatusLabel({ graph_status: 'GENERATING', bootstrap_job_state: 'RETRY_WAIT' })).toBe(
      'Retrying skill graph generation',
    );
    expect(graphStatusLabel({ graph_status: 'READY', bootstrap_job_state: 'COMPLETED' })).toBe('Skill graph ready');
    expect(isGraphInProgress('EMBEDDING')).toBe(true);
    expect(isGraphInProgress('READY')).toBe(false);
    expect(isGraphInProgress('FAILED')).toBe(false);
    expect([importanceLabel(0.9), importanceLabel(0.6), importanceLabel(0.5)]).toEqual(['Core', 'Important', 'Standard']);
  });
});

describe('bootstrap wait status', () => {
  const due = '2026-09-25T14:55:00Z';
  const before = new Date('2026-09-25T14:50:00Z');
  const waiting = {
    graph_status: 'GENERATING',
    bootstrap_job_state: 'PENDING',
    bootstrap_wait_reason: 'MODEL_BACKPRESSURE',
    bootstrap_next_attempt_at: due,
    graph_error: null,
  } as const;

  it('labels a deferred bootstrap as waiting, not generating', () => {
    expect(graphStatusLabel(waiting)).toBe('Waiting to retry skill graph generation');
    expect(graphStatusLabel({ ...waiting, bootstrap_wait_reason: 'MODEL_BUDGET_RESERVE' })).toBe(
      'Waiting to retry skill graph generation',
    );
    expect(graphStatusLabel({ ...waiting, graph_status: 'EMBEDDING' })).toBe('Waiting to retry skill indexing');
    expect(graphStatusLabel({ ...waiting, bootstrap_job_state: 'PROCESSING', bootstrap_wait_reason: null })).toBe(
      'Generating skill graph',
    );
    expect(graphStatusLabel({ ...waiting, bootstrap_job_state: 'RETRY_WAIT', bootstrap_wait_reason: 'RETRY_AFTER_ERROR' })).toBe(
      'Retrying skill graph generation',
    );
    expect(graphStatusLabel({ ...waiting, graph_status: 'FAILED' })).toBe('Skill graph generation failed');
  });

  it('explains provider backpressure and the budget reserve with the next attempt', () => {
    expect(graphWait(waiting, before)).toEqual({
      message: 'Skill graph generation is temporarily waiting for the AI provider.',
      nextAttemptAt: due,
      overdue: false,
    });
    expect(graphWait({ ...waiting, bootstrap_wait_reason: 'MODEL_BUDGET_RESERVE' }, before)?.message).toMatch(
      /AI request budget/,
    );
    expect(
      graphWait({ ...waiting, bootstrap_wait_reason: 'RETRY_AFTER_ERROR', graph_error: 'The AI provider was unavailable.' }, before)
        ?.message,
    ).toBe('The last attempt did not succeed. The AI provider was unavailable.');
  });

  it('flags a due job that no worker claims, and stays quiet otherwise', () => {
    expect(graphWait(waiting, new Date('2026-09-25T14:55:30Z'))?.overdue).toBe(false);
    expect(graphWait(waiting, new Date('2026-09-25T14:57:00Z'))?.overdue).toBe(true);
    const queued = { ...waiting, graph_status: 'PENDING', bootstrap_wait_reason: null } as const;
    expect(graphWait(queued, before)).toEqual({ message: null, nextAttemptAt: due, overdue: false });
    expect(graphWait({ ...waiting, bootstrap_next_attempt_at: null }, before)).toBeNull();
    expect(graphWait({ ...waiting, graph_status: 'READY' }, before)).toBeNull();
  });
});

describe('buildCourseRequest', () => {
  const form = (fields: Record<string, string>) => {
    const data = new FormData();
    for (const [k, v] of Object.entries(fields)) data.set(k, v);
    return data;
  };

  it('trims values and turns empty optional fields into null', () => {
    expect(buildCourseRequest(form({ name: '  Intro to Python ', subject: ' ', level: 'Beginner', description: '' }))).toEqual({
      ok: true,
      request: { name: 'Intro to Python', subject: null, level: 'Beginner', description: null },
    });
  });

  it('requires a name and enforces the API limits', () => {
    expect(buildCourseRequest(form({ name: '   ' }))).toEqual({ ok: false, error: 'Course name is required.' });
    expect(buildCourseRequest(form({ name: 'x', level: 'l'.repeat(61) }))).toMatchObject({ ok: false });
  });
});

describe('apiRequest', () => {
  it('sends the bearer token and JSON body to the configured API', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 201 }));
    const result = await apiRequest('/v1/courses', 'token-123', { method: 'POST', body: { name: 'A' } }, fetchImpl, 'http://api.test');
    expect(result).toEqual({ ok: 1 });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(url)).toBe('http://api.test/v1/courses');
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"name":"A"}');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-123');
  });

  it('surfaces the API error detail and status', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ detail: 'Course not found.' }), { status: 404 }));
    await expect(apiRequest('/v1/courses/x', 't', {}, fetchImpl, 'http://api.test')).rejects.toMatchObject({
      status: 404,
      message: 'Course not found.',
    });
  });

  it('reports an unreachable API and a missing base URL', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('fetch failed');
    });
    await expect(apiRequest('/v1/courses', 't', {}, fetchImpl, 'http://api.test')).rejects.toBeInstanceOf(ApiError);
    expect(() => apiBaseUrl('')).toThrow(/NEXT_PUBLIC_API_URL/);
  });
});
