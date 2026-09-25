import { describe, expect, it } from 'vitest';

import type { UnboundEnvelope } from '../../src/capture/CaptureManager';
import { bindEnvelopes, effectiveChoice, fetchCourses, parseCourses } from '../../src/background/courses';
import { isPopupRequest, isUnboundEnvelope } from '../../src/shared/messages';

const PY = '9440004a-a25e-4e15-94c0-17c21f6bd695';
const SQL = '5d3c1b2a-0000-4000-8000-00000000c0de';
const LEARNER = '00000000-0000-4000-8000-00000000e2e1';

function course(id: string, name: string, extra: Record<string, unknown> = {}) {
  return { id, name, role: 'STUDENT', status: 'ACTIVE', ...extra };
}

function envelope(): UnboundEnvelope {
  return {
    event_id: '00000000-0000-4000-8000-000000000001',
    schema_version: 1,
    source_provider: 'chatgpt',
    source_method: 'browser_extension',
    external_conversation_id: null,
    external_message_id: 'm1',
    external_parent_message_id: null,
    message_index: 0,
    role: 'user',
    content_text: 'How do I write a for loop?',
    content_format: 'text',
    occurred_at: null,
    captured_at: '2026-09-25T16:43:00.000Z',
    provider_model: null,
    revision_index: 0,
    attachment_metadata: [],
    context_incomplete: false,
    active_course_id: null,
    content_hash: 'a'.repeat(64),
    client_event_id: 'c'.repeat(64),
  };
}

describe('active course (H12)', () => {
  it('lists only the courses the learner studies, validated', () => {
    const body = {
      courses: [
        course(PY, 'Introduction to Python Programming'),
        course(SQL, 'x'.repeat(300)),
        course('not-a-uuid', 'Broken'),
        course('11111111-2222-4333-8444-555555555555', 'Taught course', { role: 'TEACHER' }),
        course('66666666-7777-4888-8999-000000000000', 'Archived', { status: 'ARCHIVED' }),
      ],
    };
    const courses = parseCourses(body);
    expect(courses.map((c) => c.id)).toEqual([PY, SQL]);
    expect(courses[1].name).toHaveLength(120);
    expect(parseCourses(null)).toEqual([]);
    expect(parseCourses({ courses: 'nope' })).toEqual([]);
  });

  it('keeps a stored choice only while it is still one of the learner courses', () => {
    const courses = parseCourses({ courses: [course(PY, 'Python')] });
    expect(effectiveChoice(PY, courses)).toBe(PY);
    expect(effectiveChoice(SQL, courses)).toBeNull(); // left the course: back to Auto
    expect(effectiveChoice(undefined, courses)).toBeNull();
  });

  it('binds the learner and the chosen course (Auto = null) to each envelope', () => {
    const [bound] = bindEnvelopes([envelope()], LEARNER, PY);
    expect(bound).toMatchObject({ learner_id: LEARNER, active_course_id: PY, content_text: 'How do I write a for loop?' });
    expect(bindEnvelopes([envelope()], LEARNER, null)[0].active_course_id).toBeNull();
    expect(bindEnvelopes([envelope()], LEARNER, 'drop table')[0].active_course_id).toBeNull();
  });

  it('never lets a content script pick a course', () => {
    expect(isUnboundEnvelope(envelope())).toBe(true);
    expect(isUnboundEnvelope({ ...envelope(), active_course_id: PY })).toBe(false);
  });

  it('accepts only a course id or Auto from the popup', () => {
    expect(isPopupRequest({ type: 'GET_COURSES' })).toBe(true);
    expect(isPopupRequest({ type: 'SET_ACTIVE_COURSE', courseId: PY })).toBe(true);
    expect(isPopupRequest({ type: 'SET_ACTIVE_COURSE', courseId: null })).toBe(true);
    expect(isPopupRequest({ type: 'SET_ACTIVE_COURSE', courseId: 'x' })).toBe(false);
    expect(isPopupRequest({ type: 'SET_ACTIVE_COURSE' })).toBe(false);
  });

  it('loads the courses with the session token and reports a failure instead of guessing', async () => {
    const seen: Array<[string, RequestInit | undefined]> = [];
    const ok = await fetchCourses('http://api.test/', 'token-1', async (url, init) => {
      seen.push([String(url), init]);
      return new Response(JSON.stringify({ courses: [course(PY, 'Python')] }), { status: 200 });
    });
    expect(ok).toEqual({ ok: true, courses: [{ id: PY, name: 'Python' }] });
    expect(seen[0][0]).toBe('http://api.test/v1/courses');
    expect(new Headers(seen[0][1]?.headers).get('authorization')).toBe('Bearer token-1');
    const denied = await fetchCourses('http://api.test', 't', async () => new Response('{}', { status: 401 }));
    expect(denied).toEqual({ ok: false, error: 'Could not load your courses (HTTP 401).' });
    const offline = await fetchCourses('http://api.test', 't', async () => {
      throw new TypeError('network');
    });
    expect(offline.ok).toBe(false);
  });
});
