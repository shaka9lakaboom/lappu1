/**
 * Student-experience API contracts (P5; architecture §12.1, §13): skill detail with its "Why?",
 * the enriched activity feed, feedback and recommendations.
 *
 * Mirrors services/backend/app/experience/models.py. services/backend/tests/test_contract_parity.py
 * checks the enum lists and the interface field names. No prompt, policy snapshot or model output
 * is ever part of these shapes; captured text (previews, spans) must be rendered as plain text.
 */

import type { AssessmentType, SkillNodeKind, SkillStatus } from './courses';
import type {
  AttributionStatus,
  DebtBand,
  EvidenceActor,
  EvidenceEvent,
  EvidenceType,
  FeedbackAction,
  FeedbackTargetType,
  FeedbackVerdict,
  LearningRelevance,
  LedgerEntry,
  MappingOutcome,
  MasteryState,
  OutcomeSignal,
  RecommendationState,
  RecommendationType,
  SegmentContext,
  SegmentIntent,
  SegmentRoute,
  SkillMappingStatus,
} from './intelligence';
import type { JobState, MessageRole } from './raw-activity';
import type { SourceProvider } from './index';

export const FACTOR_LEVELS = ['LOW', 'MODERATE', 'HIGH'] as const;
export type FactorLevel = (typeof FACTOR_LEVELS)[number];

export const DEBT_FACTOR_CODES = ['DELEGATION_PRESSURE', 'EVIDENCE_GAP', 'IMPORTANCE', 'CONFIDENCE', 'VERIFICATION'] as const;
export type DebtFactorCode = (typeof DEBT_FACTOR_CODES)[number];

export const MASTERY_GATE_CODES = ['ENOUGH_EVIDENCE', 'STRONG_RESULTS', 'SUSTAINED_EVIDENCE', 'INDEPENDENT_APPLICATION'] as const;
export type MasteryGateCode = (typeof MASTERY_GATE_CODES)[number];

/** Which targets each feedback action may concern (mirrors the 0007 feedback_action_target check). */
export const FEEDBACK_TARGETS: Record<FeedbackAction, readonly FeedbackTargetType[]> = {
  WRONG_SKILL: ['EVIDENCE_EVENT', 'SKILL_MAPPING'],
  DONT_COUNT: ['EVIDENCE_EVENT', 'SKILL_MAPPING', 'ACTIVITY_SEGMENT'],
  EVALUATION: ['EVIDENCE_EVENT', 'SKILL_MAPPING', 'ACTIVITY_SEGMENT', 'SKILL', 'RECOMMENDATION'],
};
export const FEEDBACK_NOTE_MAX_CHARS = 2000;

// --- Recommendations (Engine 16) --------------------------------------------------------------

/** The current (or a past) next action for one skill. */
export interface Recommendation {
  id: string;
  skill_id: string;
  canonical_name: string;
  type: RecommendationType;
  /** 0..100, higher first; NO_ACTION is 0. */
  priority: number;
  reason_code: string;
  state: RecommendationState;
  mastery_state: MasteryState;
  /** PREREQUISITE: the prerequisite to strengthen first. */
  related_skill_id: string | null;
  related_skill_name: string | null;
  debt_band: DebtBand;
  /** Actionable debt waiting behind higher-priority verifications (learner burden cap). */
  verify_deferred: boolean;
  course_ids: string[];
  created_at: string;
  updated_at: string;
}

/** Body of GET /v1/recommendations. */
export interface RecommendationsResponse {
  algorithm_version: string;
  recommendations: Recommendation[];
}

// --- Feedback (correction loop) -----------------------------------------------------------------

/** Body of POST /v1/feedback (send an Idempotency-Key header). */
export interface FeedbackRequest {
  action: FeedbackAction;
  target_type: FeedbackTargetType;
  target_id: string;
  verdict?: FeedbackVerdict | null;
  note?: string | null;
}

export interface FeedbackSummary {
  id: string;
  action: FeedbackAction;
  target_type: FeedbackTargetType;
  target_id: string;
  skill_id: string | null;
  mapping_id: string | null;
  segment_id: string | null;
  verdict: FeedbackVerdict | null;
  note: string | null;
  /** The evidence this correction excluded (one-way). */
  excluded_evidence_ids: string[];
  recomputed_skill_ids: string[];
  created_at: string;
}

/** Response of POST /v1/feedback. */
export interface FeedbackResponse {
  correlation_id: string;
  /** False for a replay of the same Idempotency-Key or an existing correction. */
  created: boolean;
  feedback: FeedbackSummary;
  ledger: LedgerEntry[];
  recommendations: Recommendation[];
}

// --- Skill detail ("Why?") -----------------------------------------------------------------------

export interface SkillInfo {
  skill_id: string;
  slug: string;
  canonical_name: string;
  description: string;
  node_kind: SkillNodeKind;
  status: SkillStatus;
  difficulty_band: number | null;
  assessment_types: AssessmentType[];
  aliases: string[];
}

export interface SkillCourseContext {
  course_id: string;
  name: string;
  importance: number;
  topic_id: string | null;
  topic_name: string | null;
}

export interface SkillPrerequisite {
  skill_id: string;
  canonical_name: string;
  mastery_state: MasteryState;
}

export interface MasteryGate {
  code: MasteryGateCode;
  met: boolean;
  current: number | null;
  required: number | null;
}

export interface MasteryExplanation {
  state: MasteryState;
  explanation_code: string;
  support: number;
  /** Null while UNKNOWN: unknown is not weak. */
  mastery_mean: number | null;
  evidence_count: number;
  performance_evidence_count: number;
  excluded_evidence_count: number;
  has_independent_application: boolean;
  gates: MasteryGate[];
  computed_as_of: string | null;
  algorithm_version: string;
}

export interface DebtFactor {
  code: DebtFactorCode;
  level: FactorLevel;
  value: number;
}

export interface DebtExplanation {
  eligible: boolean;
  /** NONE unless eligible; the qualitative signal the UI leads with. */
  band: DebtBand;
  actionable: boolean;
  /** NO_DELEGATION | INSUFFICIENT_DELEGATION | ELIGIBLE */
  eligibility_code: string;
  recent_delegation_count: number;
  min_recent_delegations: number;
  verification: string | null;
  factors: DebtFactor[];
  /** Internal 0-100 metric: an explanation driver, never the headline. */
  score: number;
}

export interface EvidenceSource {
  conversation_id: string | null;
  raw_message_ids: string[];
  captured_at: string | null;
  learner_message_preview: string | null;
}

export interface EvidenceTimelineItem {
  event: EvidenceEvent;
  reason_code: string | null;
  counts_toward_mastery: boolean;
  counts_toward_debt: boolean;
  current_weight: number;
  source: EvidenceSource | null;
  correction: FeedbackSummary | null;
}

/** Body of GET /v1/skills/{skill_id}. */
export interface SkillDetailResponse {
  skill: SkillInfo;
  courses: SkillCourseContext[];
  prerequisites: SkillPrerequisite[];
  ledger: LedgerEntry;
  mastery: MasteryExplanation;
  debt: DebtExplanation;
  evidence: EvidenceTimelineItem[];
  recommendation: Recommendation | null;
  feedback: FeedbackSummary[];
}

// --- Activity (enriched feed) ------------------------------------------------------------------

export interface ActivityMappedSkill {
  mapping_id: string;
  skill_id: string;
  canonical_name: string;
  status: SkillMappingStatus;
  confidence: number;
  evidence_span: string | null;
  attribution_status: AttributionStatus | null;
  actor: EvidenceActor | null;
  attribution_confidence: number | null;
  evidence_decision: string | null;
  evidence_id: string | null;
  evidence_type: EvidenceType | null;
  outcome_signal: OutcomeSignal | null;
  excluded: boolean;
  exclusion_reason: string | null;
  correction: FeedbackSummary | null;
}

export interface ActivitySegment {
  segment_id: string;
  segment_index: number;
  segment_count: number;
  route: SegmentRoute;
  route_reason: string;
  learning_relevance: LearningRelevance | null;
  intent: SegmentIntent | null;
  context: SegmentContext | null;
  context_incomplete: boolean;
  mapping_outcome: MappingOutcome | null;
  abstain_reason: string | null;
  mappings: ActivityMappedSkill[];
  evidence_count: number;
  excluded_evidence_count: number;
  correction: FeedbackSummary | null;
}

/** One captured message, with what SkillMirror derived from its turn. */
export interface ActivityRow {
  id: string;
  conversation_id: string;
  external_conversation_id: string | null;
  source_provider: SourceProvider;
  role: MessageRole;
  message_index: number | null;
  revision_index: number;
  captured_at: string;
  received_at: string;
  context_incomplete: boolean;
  preview: string;
  content_chars: number;
  processing_state: JobState | null;
  processing_attempts: number | null;
  processing_outcome: string | null;
  /** The message the turn was analysed from; null when not analysed yet. */
  analyzed_in: string | null;
  segments: ActivitySegment[];
}

/** Body of GET /v1/activity. */
export interface ActivityResponse {
  items: ActivityRow[];
  next_before: string | null;
}
