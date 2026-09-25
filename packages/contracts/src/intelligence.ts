/**
 * Intelligence contracts: qualification, retrieval and mapping (architecture §9, Appendix A).
 *
 * Mirrors services/backend/app/intelligence/contracts.py and migration 0003.
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
