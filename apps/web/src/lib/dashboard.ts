/**
 * The learner dashboard (P9): five questions, in the learner's words.
 *
 *   What course am I learning?          the course cards and the selected course
 *   What does SkillMirror know?         skills with a formed view (any state but UNKNOWN)
 *   What is still unknown?              UNKNOWN skills - neutral, never "weak" and never 0%
 *   What needs my attention?            current checks (VERIFY / REVERIFY recommendations)
 *   What should I do next?              the other suggestions (practice, prerequisites)
 *
 * Developer diagnostics (user id, profile timestamps, backend version) live on /settings only.
 */
import type { LedgerEntry, Recommendation } from '@skillmirror/contracts';

import { countStates, type StateCounts } from './experience';

export interface KnowledgeSummary {
  counts: StateCounts;
  total: number;
  known: number;
  unknown: number;
}

export function knowledgeSummary(entries: LedgerEntry[], courseId: string | null): KnowledgeSummary {
  const counts = countStates(entries, courseId);
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  return { counts, total, known: total - counts.UNKNOWN, unknown: counts.UNKNOWN };
}

/** One sentence: what SkillMirror has a view on, and what it does not know yet. */
export function knowledgeSentence(summary: Pick<KnowledgeSummary, 'total' | 'known' | 'unknown'>): string {
  if (summary.total === 0) return 'This course has no skills to show yet.';
  if (summary.known === 0) {
    return `SkillMirror has not formed a view on any of the ${summary.total} skills yet. Keep working as usual; it learns from your own work.`;
  }
  const skills = (n: number) => `${n} skill${n === 1 ? '' : 's'}`;
  const rest =
    summary.unknown === 0
      ? 'It has a view on every skill of this course.'
      : `${skills(summary.unknown)} ${summary.unknown === 1 ? 'is' : 'are'} not known yet - that is not a low score.`;
  return `SkillMirror has formed a view on ${skills(summary.known)} of ${summary.total}. ${rest}`;
}

const ATTENTION_TYPES = new Set<Recommendation['type']>(['VERIFY', 'REVERIFY']);

/** Current checks first ("needs my attention"), then the other suggestions ("do next"). */
export function splitRecommendations(recommendations: Recommendation[]): {
  attention: Recommendation[];
  next: Recommendation[];
} {
  return {
    attention: recommendations.filter((r) => ATTENTION_TYPES.has(r.type)),
    next: recommendations.filter((r) => !ATTENTION_TYPES.has(r.type) && r.type !== 'NO_ACTION'),
  };
}
