/**
 * Intelligence contracts: qualification, retrieval, mapping (P3A), attribution + evidence (P3B)
 * and the ledger (P4) (architecture §9, §10, Appendix A).
 *
 * Mirrors services/backend/app/intelligence/contracts.py and migrations 0003, 0005, 0006.
 * services/backend/tests/test_contract_parity.py checks the enum lists.
 */

/** Appendix A.1 context. */
export const SEGMENT_CONTEXTS = ['academic', 'professional', 'personal', 'entertainment', 'administrative', 'unknown'] as const;
export type SegmentContext = (typeof SEGMENT_CONTEXTS)[number];

/** Appendix A.1 intent. */
export const SEGMENT_INTENTS = [
  'learn',
  'understand',
  'practice',
  'solve',
  'delegate',
  'lookup',
  'create',
  'transform',
  'communicate',
  'other',
] as const;
export type SegmentIntent = (typeof SEGMENT_INTENTS)[number];

export const LEARNING_RELEVANCE_LEVELS = ['high', 'medium', 'low', 'none', 'uncertain'] as const;
export type LearningRelevance = (typeof LEARNING_RELEVANCE_LEVELS)[number];

/** §9.2 routing: MAP continues to retrieval/mapping; the others never produce evidence. */
export const SEGMENT_ROUTES = ['MAP', 'METADATA_ONLY', 'STOP', 'UNCERTAIN'] as const;
export type SegmentRoute = (typeof SEGMENT_ROUTES)[number];

export const MAPPING_OUTCOMES = ['MAPPED', 'ABSTAINED'] as const;
export type MappingOutcome = (typeof MAPPING_OUTCOMES)[number];

export const SKILL_MAPPING_STATUSES = ['ACCEPTED', 'ABSTAINED', 'REJECTED'] as const;
export type SkillMappingStatus = (typeof SKILL_MAPPING_STATUSES)[number];

export const SKILL_CANDIDATE_STATUSES = ['PENDING_REVIEW', 'APPROVED', 'MERGED', 'REJECTED'] as const;
export type SkillCandidateStatus = (typeof SKILL_CANDIDATE_STATUSES)[number];

export const MODEL_RUN_STATUSES = ['SUCCEEDED', 'INVALID_OUTPUT', 'FAILED', 'TIMEOUT', 'RATE_LIMITED', 'UNAVAILABLE'] as const;
export type ModelRunStatus = (typeof MODEL_RUN_STATUSES)[number];

/** One retrieval candidate with its per-channel scores (§9.3). */
export interface SkillCandidate {
  skill_id: string;
  canonical_name: string;
  description: string;
  node_kind: 'SKILL' | 'SUBSKILL';
  in_course: boolean;
  parent_ids: string[];
  topic_names: string[];
  semantic_similarity: number;
  lexical_score: number;
  course_context_prior: number;
  /** w_semantic * semantic + w_lexical * lexical + w_prior * prior (weights from policy_config). */
  candidate_score: number;
  rank: number;
}

/** Top-N retrieval pool and the reranked top-K ids handed to the mapper. */
export interface RetrievalResult {
  course_ids: string[];
  candidates: SkillCandidate[];
  reranked_ids: string[];
  rerank_fallback: boolean;
  query_model_run_id: string | null;
  rerank_model_run_id: string | null;
}

/** NEW_SKILL_CANDIDATE (Appendix A.2): stored for review, never an active skill by itself. */
export interface NewSkillCandidate {
  canonical_name: string;
  parent_candidate_id: string | null;
  description: string | null;
}

// --- P3B: attribution + evidence (§9.5, §9.6, §10.1, Appendix A.3; migration 0005) ---------

/** Who performed a mapped skill in a segment (§9.5). Categorical: no contribution percentages. */
export const EVIDENCE_ACTORS = ['STUDENT', 'AI', 'SHARED', 'UNKNOWN'] as const;
export type EvidenceActor = (typeof EVIDENCE_ACTORS)[number];

/** §9.6 evidence types. EXPOSURE and OBSERVATION never carry strength or an outcome. */
export const EVIDENCE_TYPES = [
  'EXPOSURE',
  'OBSERVATION',
  'ASSISTED_ATTEMPT',
  'INDEPENDENT_EXPLANATION',
  'INDEPENDENT_APPLICATION',
  'TRANSFER',
  'VERIFICATION',
  'EXECUTION_RESULT',
  'TEACHER_EVIDENCE',
] as const;
export type EvidenceType = (typeof EVIDENCE_TYPES)[number];

/** Appendix A.3: what the attribution model may propose (OTHER = no evidence). */
export const ATTRIBUTION_EVIDENCE_TYPES = [
  'EXPOSURE',
  'OBSERVATION',
  'ASSISTED_ATTEMPT',
  'INDEPENDENT_EXPLANATION',
  'INDEPENDENT_APPLICATION',
  'TRANSFER',
  'OTHER',
] as const;
export type AttributionEvidenceType = (typeof ATTRIBUTION_EVIDENCE_TYPES)[number];

/** Quality of the learner's own performance (ADR 0005): CORRECT 1, PARTIAL policy value, INCORRECT 0. */
export const OUTCOME_SIGNALS = ['CORRECT', 'INCORRECT', 'PARTIAL', 'NOT_APPLICABLE'] as const;
export type OutcomeSignal = (typeof OUTCOME_SIGNALS)[number];

export const EVIDENCE_SOURCE_TYPES = ['AI_ACTIVITY', 'VERIFICATION', 'ASSESSMENT', 'TEACHER'] as const;
export type EvidenceSourceType = (typeof EVIDENCE_SOURCE_TYPES)[number];

export const ATTRIBUTION_STATUSES = ['ATTRIBUTED', 'ABSTAINED'] as const;
export type AttributionStatus = (typeof ATTRIBUTION_STATUSES)[number];

/** One SKILL_ATTRIBUTION result for an ACCEPTED skill mapping (Appendix A.3 + outcome signal). */
export interface AttributionItem {
  skill_id: string;
  actor: EvidenceActor;
  confidence: number;
  student_evidence_span: string | null;
  ai_evidence_span: string | null;
  evidence_type: AttributionEvidenceType;
  outcome_signal: OutcomeSignal;
  reason_code: string;
}

/** SKILL_ATTRIBUTION output: exactly one item per supplied accepted skill. */
export interface AttributionOutput {
  attributions: AttributionItem[];
}

/** One immutable EvidenceEvent (§10.1). */
export interface EvidenceEvent {
  id: string;
  learner_id: string;
  skill_id: string;
  source_type: EvidenceSourceType;
  source_id: string;
  attribution_id: string | null;
  evidence_type: EvidenceType;
  actor: EvidenceActor;
  outcome_signal: OutcomeSignal;
  /** 0..1 only where performance exists. */
  outcome: number | null;
  difficulty: number;
  independence: number;
  /** base_weight * difficulty_multiplier * independence * evidence_confidence. */
  strength: number;
  mapping_confidence: number;
  attribution_confidence: number;
  /** Set only for graded evidence (P6 verification); null for captured AI activity. */
  grading_confidence: number | null;
  /** min(mapping, attribution, grading if any) - Appendix B.2. */
  evidence_confidence: number;
  evidence_span: Record<string, string | null>;
  model_run_ids: string[];
  excluded: boolean;
  occurred_at: string;
  created_at: string;
}

// --- P4: ledger + AI Assistance Debt (§10.2-§10.4; migration 0006) -------------------------

/** §10.3. VERIFIED / NEEDS_REVERIFICATION need SkillMirror verification (P6). */
export const MASTERY_STATES = [
  'UNKNOWN',
  'EMERGING',
  'DEVELOPING',
  'DEMONSTRATED',
  'VERIFIED',
  'NEEDS_REVERIFICATION',
] as const;
export type MasteryState = (typeof MASTERY_STATES)[number];

/** One skill of GET /v1/ledger. A skill without evidence is UNKNOWN with a null mean. */
export interface SkillLedgerSummary {
  skill_id: string;
  slug: string;
  canonical_name: string;
  description: string;
  node_kind: 'SKILL' | 'SUBSKILL';
  difficulty_band: number | null;
  course_ids: string[];
  importance: number;
  mastery_state: MasteryState;
  /** Null while UNKNOWN: unknown is not weak. */
  mastery_mean: number | null;
  alpha: number;
  beta: number;
  support: number;
  /** 0..100; 0 unless debt_eligible (repeated recent AI/SHARED delegation). */
  debt_score: number;
  debt_eligible: boolean;
  debt_actionable: boolean;
  evidence_count: number;
  performance_evidence_count: number;
  recent_delegation_count: number;
  last_evidence_at: string | null;
  ledger_version: number | null;
  computed_as_of: string | null;
}

/** Body of GET /v1/ledger. */
export interface LedgerResponse {
  algorithm_version: string;
  skills: SkillLedgerSummary[];
}
