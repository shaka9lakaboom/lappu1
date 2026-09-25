/**
 * Plain-language view models for the student experience (P5).
 *
 * Product rules carried by this module:
 * - UNKNOWN is not weak: it reads "Not enough activity yet", uses a neutral tone and never a mean.
 * - AI use is not dependency: debt is a reliance *signal* with its evidence, never a verdict.
 * - Nothing here is red: no skill state is presented as failure.
 */
import type {
  ActivitySegment,
  CourseSkill,
  CourseSkillsResponse,
  DebtBand,
  DebtExplanation,
  DebtFactor,
  EvidenceActor,
  EvidenceType,
  LedgerEntry,
  MasteryState,
  OutcomeSignal,
  Recommendation,
  SegmentRoute,
  SkillNode,
} from '@skillmirror/contracts';

import { groupSkillsByTopic } from './courses';

export type Tone = 'neutral' | 'sky' | 'indigo' | 'emerald' | 'emerald-strong' | 'amber';

export const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'border-dashed border-border bg-muted text-muted-foreground',
  sky: 'border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200',
  indigo: 'border-indigo-200 bg-indigo-50 text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-200',
  emerald: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
  'emerald-strong': 'border-emerald-700 bg-emerald-700 text-white dark:border-emerald-500 dark:bg-emerald-600',
  amber: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200',
};

export interface StatePresentation {
  label: string;
  tone: Tone;
  /** One-sentence headline explanation. */
  headline: string;
}

export const MASTERY: Record<MasteryState, StatePresentation> = {
  UNKNOWN: {
    label: 'Not enough activity yet',
    tone: 'neutral',
    headline: 'SkillMirror does not have enough independent evidence yet.',
  },
  EMERGING: {
    label: 'Emerging',
    tone: 'sky',
    headline: 'Your early independent attempts show this skill is still taking shape.',
  },
  DEVELOPING: {
    label: 'Developing',
    tone: 'indigo',
    headline: 'Your independent evidence shows progress, but not yet enough to count as demonstrated.',
  },
  DEMONSTRATED: {
    label: 'Demonstrated',
    tone: 'emerald',
    headline: 'Your recent independent evidence supports this skill.',
  },
  VERIFIED: {
    label: 'Verified',
    tone: 'emerald-strong',
    headline: 'A recent SkillMirror check confirmed this skill.',
  },
  NEEDS_REVERIFICATION: {
    label: 'Needs a fresh check',
    tone: 'amber',
    headline: 'Your earlier SkillMirror check needs refreshing; a short fresh check would confirm the skill.',
  },
};

/** Display order for state counts (UNKNOWN first, never ranked as the "worst"). */
export const STATE_ORDER: MasteryState[] = [
  'UNKNOWN',
  'EMERGING',
  'DEVELOPING',
  'DEMONSTRATED',
  'VERIFIED',
  'NEEDS_REVERIFICATION',
];

const EXPLANATION_DETAILS: Record<string, string> = {
  NO_EVIDENCE: 'No captured activity has involved this skill so far. That says nothing about your ability.',
  NO_INDEPENDENT_PERFORMANCE:
    'So far you have seen explanations, or the AI did this skill for you. That is not evidence of your own ability either way.',
  NOT_ENOUGH_SUPPORT: 'There is some evidence of your own work, but not enough yet to form a view.',
  EARLY_DIFFICULTY: 'Your first independent attempts had difficulties. A few more attempts will change this quickly.',
  MIXED_RESULTS: 'Your independent results so far are mixed.',
  NEEDS_MORE_EVIDENCE: 'Your results are strong, but there is not yet enough independent evidence.',
  NEEDS_INDEPENDENT_APPLICATION:
    'Your results are strong; one successful independent application would complete the picture.',
  INDEPENDENT_EVIDENCE_SUPPORTS: 'You applied this skill yourself, successfully, more than once.',
  RECENT_VERIFICATION: 'You passed a recent SkillMirror check, and your independent evidence supports it.',
  VERIFICATION_HELD:
    'You passed a recent SkillMirror check. A later result did not go as well; it is recorded, but one result does not undo the check.',
  VERIFICATION_STALE: 'Your last check was a while ago.',
  VERIFICATION_CONTRADICTED: 'Several recent results disagree with your earlier check, so a fresh check is suggested.',
};

export function explanationDetail(code: string): string {
  return EXPLANATION_DETAILS[code] ?? 'SkillMirror derived this state from your evidence.';
}

export const GATE_LABELS: Record<string, string> = {
  ENOUGH_EVIDENCE: 'Enough independent evidence to form a view',
  STRONG_RESULTS: 'Mostly successful results',
  SUSTAINED_EVIDENCE: 'Evidence from several independent attempts',
  INDEPENDENT_APPLICATION: 'At least one successful independent application',
  RECENT_CHECK_PASSED: 'A recent SkillMirror check passed',
  VERIFIED_RESULTS: 'Consistently successful results (verified level)',
  VERIFIED_EVIDENCE: 'Enough independent evidence for verified (including the check)',
};

// --- AI Assistance Debt ---------------------------------------------------------------------------

export const DEBT_BAND: Record<DebtBand, { label: string; tone: Tone }> = {
  NONE: { label: 'No reliance signal', tone: 'neutral' },
  LOW: { label: 'Low reliance signal', tone: 'neutral' },
  MODERATE: { label: 'Moderate reliance signal', tone: 'amber' },
  HIGH: { label: 'High reliance signal', tone: 'amber' },
};

export const NOT_ENOUGH_DELEGATION = 'There is not enough repeated AI delegation here to infer reliance.';

export const DEBT_NOT_A_JUDGEMENT =
  'This is not a judgement of you or of using AI. It only means SkillMirror cannot yet tell whether you can do this on your own.';

/** The headline and supporting sentence of the AI Assistance Debt panel. Never the 0-100 score. */
export function debtSummary(debt: DebtExplanation): { headline: string; detail: string } {
  if (!debt.eligible) {
    const seen = debt.recent_delegation_count;
    const detail =
      seen === 0
        ? 'SkillMirror has not seen the AI perform this skill for you recently.'
        : `SkillMirror saw the AI perform this skill for you ${seen === 1 ? 'once' : `${seen} times`} recently; at least ${debt.min_recent_delegations} confident, repeated instances are needed before it infers anything.`;
    return { headline: NOT_ENOUGH_DELEGATION, detail };
  }
  const detail =
    debt.band === 'LOW'
      ? 'The AI has done this skill for you a few times recently, and your own evidence covers much of it.'
      : `The AI has repeatedly done this skill for you recently (${debt.recent_delegation_count} times), and there is little independent evidence of your own yet.`;
  return { headline: DEBT_BAND[debt.band].label, detail };
}

const FACTOR_TEXT: Record<DebtFactor['code'], string> = {
  DELEGATION_PRESSURE: 'Delegation pressure: how often, and how recently, the AI performed this skill for you.',
  EVIDENCE_GAP: 'Independent evidence gap: how much evidence of your own work is still missing.',
  IMPORTANCE: 'Importance: how central this skill is to your course.',
  CONFIDENCE: 'Confidence: how sure SkillMirror is about those AI-performed instances.',
  VERIFICATION: 'Verification state: whether a SkillMirror check has confirmed the skill.',
};

const VERIFICATION_TEXT: Record<string, string> = {
  UNVERIFIED: 'Not verified yet.',
  RECENTLY_PASSED: 'You recently passed a check.',
  FAILED: 'A recent check has not gone well yet.',
};

export function factorText(factor: DebtFactor, verification: string | null): string {
  if (factor.code === 'VERIFICATION') {
    return `${FACTOR_TEXT.VERIFICATION} ${VERIFICATION_TEXT[verification ?? 'UNVERIFIED'] ?? ''}`.trim();
  }
  return FACTOR_TEXT[factor.code];
}

export const LEVEL_LABEL: Record<DebtFactor['level'], string> = { LOW: 'Low', MODERATE: 'Moderate', HIGH: 'High' };

// --- Recommendations ----------------------------------------------------------------------------

export function recommendationText(rec: Pick<Recommendation, 'type' | 'reason_code' | 'related_skill_name'>): {
  title: string;
  body: string;
} {
  switch (rec.type) {
    case 'VERIFY':
      return {
        title: 'Show you can do it on your own',
        body: 'The AI has repeatedly done this for you and there is no independent evidence yet. A short SkillMirror check, done without AI help, adds that evidence.',
      };
    case 'REVERIFY':
      return rec.reason_code === 'VERIFICATION_CONTRADICTED'
        ? {
            title: 'Refresh your check',
            body: 'Several recent results disagree with your earlier check. A short fresh check will show where you stand.',
          }
        : { title: 'Refresh your check', body: 'Your last check was a while ago. A short fresh check will confirm the skill.' };
    case 'PREREQUISITE':
      return {
        title: `Strengthen ${rec.related_skill_name ?? 'a prerequisite'} first`,
        body: `This skill builds on ${rec.related_skill_name ?? 'a prerequisite'}, where your early attempts show difficulty.`,
      };
    case 'PRACTICE':
      return rec.reason_code === 'EMERGING_NEEDS_PRACTICE'
        ? {
            title: 'Keep practising',
            body: 'Try a few small problems on your own. SkillMirror updates as your independent work is captured.',
          }
        : {
            title: 'Build consistency',
            body: 'A few more independent attempts will show whether this skill is solid.',
          };
    case 'NO_ACTION':
      if (rec.reason_code === 'INDEPENDENT_EVIDENCE_SUFFICIENT') {
        return { title: 'No action needed', body: 'Your independent evidence supports this skill.' };
      }
      if (rec.reason_code === 'RECENTLY_VERIFIED') {
        return { title: 'No action needed', body: 'A recent check confirmed this skill.' };
      }
      return {
        title: 'Nothing to do yet',
        body: 'Keep working as usual. SkillMirror will form a view once it sees your own work on this skill.',
      };
  }
}

// --- Evidence and activity labels ------------------------------------------------------------------

export const EVIDENCE_TYPE_LABEL: Record<EvidenceType, string> = {
  EXPOSURE: 'Saw an explanation',
  OBSERVATION: 'The AI did it',
  ASSISTED_ATTEMPT: 'Assisted attempt',
  INDEPENDENT_EXPLANATION: 'Explained it yourself',
  INDEPENDENT_APPLICATION: 'Applied it yourself',
  TRANSFER: 'Applied it in a new context',
  VERIFICATION: 'SkillMirror check',
  EXECUTION_RESULT: 'Checked by running it',
  TEACHER_EVIDENCE: 'Teacher assessment',
};

// --- What a recorded evidence means for the learner (P9) ------------------------------------------
//
// Derived ONLY from the recorded evidence - its type and exclusion after the deterministic
// qualification - never from the attributor's claim or the raw model output. The "who" follows
// from the type (the qualification makes type and actor consistent: an AI actor is never learner
// performance, a SHARED actor is an assisted attempt), so a chip can never say "You" next to
// "The AI did it".

export type EvidenceKind = 'LEARNER' | 'ASSISTED' | 'AI_ASSISTANCE' | 'EXPOSURE';

export function evidenceKind(type: EvidenceType): EvidenceKind {
  if (type === 'EXPOSURE') return 'EXPOSURE';
  if (type === 'OBSERVATION') return 'AI_ASSISTANCE';
  if (type === 'ASSISTED_ATTEMPT') return 'ASSISTED';
  return 'LEARNER'; // independent explanation / application / transfer, checks, teacher evidence
}

export const WHO_QUESTION = 'Who demonstrated this skill?';

export const EVIDENCE_KIND: Record<EvidenceKind, { who: string | null; effect: string; summary: string }> = {
  LEARNER: {
    who: 'You',
    effect: 'Independent learner evidence: counts toward mastery',
    summary: 'Independent learner evidence recorded',
  },
  ASSISTED: {
    who: 'You + AI',
    effect: 'Shared work: counts with reduced weight',
    summary: 'Assisted-attempt evidence recorded (you + AI)',
  },
  AI_ASSISTANCE: {
    who: 'AI',
    effect: 'AI-assistance evidence: no mastery credit',
    summary: 'AI-assistance evidence recorded — no mastery credit',
  },
  EXPOSURE: {
    who: null,
    effect: 'Exposure only: does not affect mastery',
    summary: 'Exposure only — does not affect mastery',
  },
};

/** "Who demonstrated this skill?" for a recorded evidence type; exposure has no performer. */
export function whoLabel(type: EvidenceType): string {
  const who = EVIDENCE_KIND[evidenceKind(type)].who;
  return who ? `Demonstrated by: ${who}` : 'Explanation seen';
}

export const ACTOR_LABEL: Record<EvidenceActor, string> = {
  STUDENT: 'You',
  AI: 'AI',
  SHARED: 'You + AI',
  UNKNOWN: 'Unclear',
};

export const OUTCOME_LABEL: Record<OutcomeSignal, string> = {
  CORRECT: 'Correct',
  PARTIAL: 'Partly correct',
  INCORRECT: 'Not correct yet',
  NOT_APPLICABLE: 'No result (not a performance)',
};

/**
 * Why a recorded evidence differs from what the attributor proposed (deterministic qualification
 * rules). Null when the evidence was recorded as proposed.
 */
export function qualificationNote(reason: string | null | undefined): string | null {
  switch (reason) {
    case 'COPIED_FROM_AI':
      return "Your text matches an earlier AI answer in this conversation, so it counts as the AI's work.";
    case 'AI_ACTOR_NOT_PERFORMANCE':
      return 'The AI did this part, so it is not counted as your performance.';
    case 'SHARED_NOT_INDEPENDENT':
      return 'You did this together with the AI, so it counts as an assisted attempt.';
    default:
      return null;
  }
}

/** The "Your words" span label; text the copy guard found in earlier AI output is not the learner's. */
export function studentSpanLabel(reason: string | null | undefined): string {
  return reason === 'COPIED_FROM_AI' ? 'Your text (also in an earlier AI answer)' : 'Your words';
}

export function independenceLabel(independence: number): string {
  if (independence >= 0.8) return 'Independent';
  if (independence > 0) return 'Assisted';
  return 'Not independent';
}

export function exclusionLabel(reason: string | null): string {
  if (reason === 'LEARNER_WRONG_SKILL') return 'You marked this as the wrong skill';
  if (reason === 'LEARNER_DONT_COUNT') return 'You chose not to count this';
  return 'Not counted';
}

export const ROUTE_LABEL: Record<SegmentRoute, string> = {
  MAP: 'Learning activity',
  // LEARNING_RELEVANT + NOT_SKILL_BEARING: a learning question, but no skill was performed.
  METADATA_ONLY: 'Learning activity — no skill evidence',
  STOP: 'Not learning activity (ignored)',
  UNCERTAIN: 'Unclear (kept, no evidence)',
};

/**
 * STOP covers learning relevance "low" as well as "none" (policy: only high / medium are learning
 * activity). A "low" unit was recorded as somewhat learning-related - e.g. a one-line "what is
 * python" - so it is not called "not learning"; it simply carries no skill evidence.
 */
export const LOW_RELEVANCE_LABEL = 'Low learning relevance — no skill evidence';

type SegmentLike = Pick<ActivitySegment, 'route' | 'mapping_outcome' | 'mappings'> &
  Partial<Pick<ActivitySegment, 'learning_relevance'>>;

export function segmentOutcomeLabel(segment: SegmentLike): string {
  if (segment.route === 'STOP' && segment.learning_relevance === 'low') return LOW_RELEVANCE_LABEL;
  if (segment.route !== 'MAP') return ROUTE_LABEL[segment.route];
  if (segment.mapping_outcome === 'MAPPED') {
    const accepted = segment.mappings.filter((m) => m.status === 'ACCEPTED').length;
    return `Matched to ${accepted} skill${accepted === 1 ? '' : 's'}`;
  }
  if (segment.mapping_outcome === 'ABSTAINED') return 'No confident skill match (no evidence)';
  return 'Waiting for skill matching';
}

const OUTCOME_TEXT: Record<string, string> = {
  // Never shown bare: what the evidence means comes from the turn's recorded evidence.
  EVIDENCE_RECORDED: 'Analysed — see the matched skills for what counts',
  MAPPED_NO_EVIDENCE: 'Matched to a skill — no skill evidence qualified',
  ALREADY_ANALYZED: 'Analysed with its turn',
  DEFERRED_TO_ASSISTANT: "Analysed together with the AI's reply",
  NON_LEARNING: 'Not learning activity',
  ABSTAINED: 'No confident skill match — no skill evidence',
  METADATA_ONLY: ROUTE_LABEL.METADATA_ONLY,
  UNCERTAIN: 'Unclear — kept without evidence',
};

/** The job outcome in words, without the turn's details (see turnOutcomeLabel). */
export function processingOutcomeLabel(outcome: string | null): string | null {
  if (!outcome) return null;
  return OUTCOME_TEXT[outcome] ?? outcome.toLowerCase().replaceAll('_', ' ');
}

const SUMMARY_ORDER: EvidenceKind[] = ['LEARNER', 'ASSISTED', 'AI_ASSISTANCE', 'EXPOSURE'];

/** What the turn's recorded evidence means: the strongest kind that still counts. */
export function turnEvidenceSummary(segments: ActivitySegment[]): string | null {
  const recorded = segments.flatMap((s) => s.mappings).filter((m) => m.status === 'ACCEPTED' && m.evidence_type);
  if (recorded.length === 0) return null;
  const kinds = new Set(recorded.filter((m) => !m.excluded).map((m) => evidenceKind(m.evidence_type!)));
  if (kinds.size === 0) return 'Evidence recorded — not counted, at your request';
  return EVIDENCE_KIND[SUMMARY_ORDER.find((k) => kinds.has(k))!].summary;
}

/**
 * The turn's outcome for the learner. `segments` are the turn's task units (on its anchor
 * message) when known: evidence wording then follows the recorded evidence, and a
 * low-relevance unit is not called "not learning".
 */
export function turnOutcomeLabel(outcome: string | null, segments: ActivitySegment[] | null): string | null {
  if (!outcome) return null;
  if (segments && outcome === 'EVIDENCE_RECORDED') return turnEvidenceSummary(segments) ?? processingOutcomeLabel(outcome);
  if (segments && outcome === 'NON_LEARNING' && segments.some((s) => s.route === 'STOP' && s.learning_relevance === 'low')) {
    return LOW_RELEVANCE_LABEL;
  }
  return processingOutcomeLabel(outcome);
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return `${new Date(iso).toISOString().replace('T', ' ').slice(0, 16)} UTC`;
}

// --- Aggregates -----------------------------------------------------------------------------------

export type StateCounts = Record<MasteryState, number>;

export function emptyCounts(): StateCounts {
  return { UNKNOWN: 0, EMERGING: 0, DEVELOPING: 0, DEMONSTRATED: 0, VERIFIED: 0, NEEDS_REVERIFICATION: 0 };
}

/** Mastery-state counts of the ledger entries, optionally within one course. */
export function countStates(entries: LedgerEntry[], courseId?: string | null): StateCounts {
  const counts = emptyCounts();
  for (const entry of entries) {
    if (courseId && !entry.course_ids.includes(courseId)) continue;
    counts[entry.mastery_state] += 1;
  }
  return counts;
}

export interface MappedSkill {
  entry: CourseSkill;
  state: MasteryState;
  ledger: LedgerEntry | null;
}

export interface MapGroup {
  topic: SkillNode | null;
  skills: MappedSkill[];
}

/** One line per topic card (P9): how many of its skills SkillMirror has a view on. */
export function topicSummary(group: Pick<MapGroup, 'skills'>): string {
  const total = group.skills.length;
  const known = group.skills.filter((s) => s.state !== 'UNKNOWN').length;
  if (known === 0) return `${total} skill${total === 1 ? '' : 's'} · none known yet`;
  return `${known} of ${total} with a view · ${total - known} not known yet`;
}

/**
 * The course graph (topic -> skills) with the learner's ledger overlaid. A graph skill without a
 * ledger entry is UNKNOWN - never 0%.
 */
export function overlaySkillMap(graph: CourseSkillsResponse, ledger: LedgerEntry[]): MapGroup[] {
  const byId = new Map(ledger.map((entry) => [entry.skill_id, entry]));
  return groupSkillsByTopic(graph).map((group) => ({
    topic: group.topic,
    skills: group.skills.map((entry) => {
      const found = byId.get(entry.skill.id) ?? null;
      return { entry, ledger: found, state: found?.mastery_state ?? 'UNKNOWN' };
    }),
  }));
}
