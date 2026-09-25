/**
 * P9 learner dashboard: it answers the learner's five questions, keeps UNKNOWN neutral, and holds
 * no developer diagnostics (those moved to /settings).
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { AttentionPanel, KnowledgePanel, NextStepsPanel } from '@/components/experience/dashboard-sections';
import { knowledgeSentence, knowledgeSummary, splitRecommendations } from '@/lib/dashboard';
import { COURSE, ledgerEntry, recommendation } from '@/lib/test-data';

const html = (node: React.ReactElement) => renderToStaticMarkup(node);
const app = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app');

describe('dashboard view model', () => {
  it('separates what SkillMirror knows from what it does not know yet', () => {
    const entries = [
      ledgerEntry({ skill_id: 'a', mastery_state: 'DEMONSTRATED', course_ids: [COURSE] }),
      ledgerEntry({ skill_id: 'b', mastery_state: 'UNKNOWN', course_ids: [COURSE] }),
      ledgerEntry({ skill_id: 'c', mastery_state: 'UNKNOWN', course_ids: [COURSE] }),
      ledgerEntry({ skill_id: 'd', mastery_state: 'EMERGING', course_ids: ['other'] }),
    ];
    const summary = knowledgeSummary(entries, COURSE);
    expect(summary).toMatchObject({ total: 3, known: 1, unknown: 2 });
    expect(knowledgeSentence(summary)).toBe(
      'SkillMirror has formed a view on 1 skill of 3. 2 skills are not known yet - that is not a low score.',
    );
    expect(knowledgeSentence({ total: 4, known: 0, unknown: 4 })).toContain('has not formed a view on any');
    expect(knowledgeSentence({ total: 0, known: 0, unknown: 0 })).toBe('This course has no skills to show yet.');
  });

  it('puts current checks under attention and practice under next steps', () => {
    const verify = recommendation({ id: 'v', type: 'VERIFY' });
    const reverify = recommendation({ id: 'r', type: 'REVERIFY', reason_code: 'VERIFICATION_STALE' });
    const practice = recommendation({ id: 'p', type: 'PRACTICE', reason_code: 'DEVELOPING_NEEDS_PRACTICE' });
    const none = recommendation({ id: 'n', type: 'NO_ACTION', reason_code: 'NOT_ENOUGH_EVIDENCE' });
    const { attention, next } = splitRecommendations([verify, practice, reverify, none]);
    expect(attention.map((r) => r.id)).toEqual(['v', 'r']);
    expect(next.map((r) => r.id)).toEqual(['p']);
  });
});

describe('dashboard sections', () => {
  it('shows the knowledge summary with UNKNOWN as neutral, never a low score', () => {
    const markup = html(
      <KnowledgePanel
        courseName="Python basics"
        summary={knowledgeSummary([ledgerEntry({ mastery_state: 'UNKNOWN', course_ids: [COURSE] })], COURSE)}
      />,
    );
    expect(markup).toContain('What SkillMirror knows about Python basics');
    expect(markup).toContain('not a low score');
    expect(markup).toContain('data-state="UNKNOWN"');
    expect(markup).toContain('href="/skills"');
  });

  it('says plainly when nothing needs attention, and lists current checks otherwise', () => {
    expect(html(<AttentionPanel recommendations={[]} />)).toContain('Nothing needs your attention right now.');
    const markup = html(<AttentionPanel recommendations={[recommendation({ type: 'VERIFY' })]} />);
    expect(markup).toContain('data-testid="recommendation-verify"');
    expect(html(<NextStepsPanel recommendations={[]} />)).toContain('Nothing to do yet');
  });
});

describe('no developer diagnostics on the learner dashboard', () => {
  const dashboard = readFileSync(join(app, 'dashboard', 'page.tsx'), 'utf8');
  const settings = readFileSync(join(app, 'settings', 'page.tsx'), 'utf8');

  it('does not show the raw user id, profile timestamps or the backend health card', () => {
    for (const marker of ['user-id', 'profile-created-at', 'api-status', "'/health'", 'HealthResponse', 'user.id']) {
      expect(dashboard).not.toContain(marker);
    }
    expect(dashboard).toContain('href="/settings"');
  });

  it('keeps them on Settings & diagnostics', () => {
    for (const marker of ['user-id', 'profile-created-at', 'api-status', "'/health'"]) {
      expect(settings).toContain(marker);
    }
  });
});
