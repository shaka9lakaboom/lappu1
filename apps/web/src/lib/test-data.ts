/**
 * Typed sample API payloads for the web unit tests (never imported by the app).
 * Shapes follow packages/contracts exactly, so a contract change breaks the tests' typecheck.
 */
import type {
  ActivityRow,
  DebtExplanation,
  EvidenceTimelineItem,
  LedgerEntry,
  MasteryExplanation,
  Recommendation,
  VerificationChallenge,
  VerificationSessionSummary,
  VerificationsResponse,
} from '@skillmirror/contracts';

export const SKILL = '11111111-1111-4111-8111-111111111111';
export const OTHER_SKILL = '22222222-2222-4222-8222-222222222222';
export const COURSE = '33333333-3333-4333-8333-333333333333';

export function ledgerEntry(overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    skill_id: SKILL,
    slug: 'for-loops',
    canonical_name: 'Writing for loops',
    description: 'Iterate over a list.',
    node_kind: 'SKILL',
    difficulty_band: 2,
    course_ids: [COURSE],
    importance: 0.5,
    mastery_state: 'UNKNOWN',
    mastery_mean: null,
    alpha: 1,
    beta: 1,
    support: 0,
    debt_score: 0,
    debt_eligible: false,
    debt_actionable: false,
    debt_band: 'NONE',
    evidence_count: 0,
    performance_evidence_count: 0,
    recent_delegation_count: 0,
    last_evidence_at: null,
    ledger_version: null,
    computed_as_of: null,
    ...overrides,
  };
}

export function mastery(overrides: Partial<MasteryExplanation> = {}): MasteryExplanation {
  return {
    state: 'UNKNOWN',
    explanation_code: 'NO_EVIDENCE',
    support: 0,
    mastery_mean: null,
    evidence_count: 0,
    performance_evidence_count: 0,
    excluded_evidence_count: 0,
    has_independent_application: false,
    gates: [{ code: 'ENOUGH_EVIDENCE', met: false, current: 0, required: 1 }],
    computed_as_of: null,
    algorithm_version: 'ledger/p4-v1',
    ...overrides,
  };
}

export function debt(overrides: Partial<DebtExplanation> = {}): DebtExplanation {
  return {
    eligible: false,
    band: 'NONE',
    actionable: false,
    eligibility_code: 'NO_DELEGATION',
    recent_delegation_count: 0,
    min_recent_delegations: 2,
    verification: null,
    factors: [],
    score: 0,
    ...overrides,
  };
}

export const ELIGIBLE_DEBT = debt({
  eligible: true,
  band: 'MODERATE',
  actionable: true,
  eligibility_code: 'ELIGIBLE',
  recent_delegation_count: 3,
  verification: 'UNVERIFIED',
  score: 20.03,
  factors: [
    { code: 'DELEGATION_PRESSURE', level: 'HIGH', value: 0.74 },
    { code: 'EVIDENCE_GAP', level: 'HIGH', value: 1 },
    { code: 'IMPORTANCE', level: 'MODERATE', value: 0.5 },
    { code: 'CONFIDENCE', level: 'HIGH', value: 0.9 },
    { code: 'VERIFICATION', level: 'MODERATE', value: 0.6 },
  ],
});

export function recommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    skill_id: SKILL,
    canonical_name: 'Writing for loops',
    type: 'NO_ACTION',
    priority: 0,
    reason_code: 'NOT_ENOUGH_EVIDENCE',
    state: 'ACTIVE',
    mastery_state: 'UNKNOWN',
    related_skill_id: null,
    related_skill_name: null,
    debt_band: 'NONE',
    verify_deferred: false,
    course_ids: [COURSE],
    created_at: '2026-09-25T10:00:00Z',
    updated_at: '2026-09-25T10:00:00Z',
    ...overrides,
  };
}

export function evidenceItem(overrides: Partial<EvidenceTimelineItem['event']> = {}, item: Partial<EvidenceTimelineItem> = {}): EvidenceTimelineItem {
  return {
    event: {
      id: '55555555-5555-4555-8555-555555555555',
      learner_id: '66666666-6666-4666-8666-666666666666',
      skill_id: SKILL,
      source_type: 'AI_ACTIVITY',
      source_id: '77777777-7777-4777-8777-777777777777',
      attribution_id: '77777777-7777-4777-8777-777777777777',
      mapping_id: '88888888-8888-4888-8888-888888888888',
      decision_id: '99999999-9999-4999-8999-999999999999',
      segment_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      raw_message_ids: ['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'],
      evidence_type: 'INDEPENDENT_APPLICATION',
      actor: 'STUDENT',
      outcome_signal: 'CORRECT',
      outcome: 1,
      difficulty: 0.25,
      independence: 1,
      strength: 0.7875,
      mapping_confidence: 0.9,
      attribution_confidence: 0.95,
      grading_confidence: null,
      evidence_confidence: 0.9,
      evidence_span: { student: 'for name in names: print(name)', ai: null, mapping: 'for name in names' },
      model_run_ids: [],
      qualification_reason: 'QUALIFIED',
      excluded: false,
      exclusion_reason: null,
      excluded_at: null,
      occurred_at: '2026-09-25T10:00:00Z',
      created_at: '2026-09-25T10:00:05Z',
      ...overrides,
    },
    reason_code: 'STUDENT_WROTE_CODE',
    counts_toward_mastery: !overrides.excluded,
    counts_toward_debt: false,
    current_weight: overrides.excluded ? 0 : 0.7875,
    source: {
      conversation_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      raw_message_ids: ['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'],
      captured_at: '2026-09-25T10:00:00Z',
      learner_message_preview: 'I wrote this myself: `for name in names: print(name)`. Is it right?',
    },
    correction: null,
    ...item,
  };
}

export function activityRow(overrides: Partial<ActivityRow> = {}): ActivityRow {
  return {
    id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    conversation_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    external_conversation_id: 'conv-123456789',
    source_provider: 'chatgpt',
    role: 'user',
    message_index: 0,
    revision_index: 0,
    captured_at: '2026-09-25T10:00:00Z',
    received_at: '2026-09-25T10:00:01Z',
    context_incomplete: false,
    preview: 'I wrote this myself: `for name in names: print(name)`. Is it right?',
    content_chars: 64,
    processing_state: 'COMPLETED',
    processing_attempts: 1,
    processing_outcome: 'EVIDENCE_RECORDED',
    analyzed_in: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    segments: [
      {
        segment_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        segment_index: 0,
        segment_count: 1,
        route: 'MAP',
        route_reason: 'LEARNING_SKILL_BEARING',
        learning_relevance: 'high',
        intent: 'practice',
        context: 'academic',
        context_incomplete: false,
        mapping_outcome: 'MAPPED',
        abstain_reason: null,
        mappings: [
          {
            mapping_id: '88888888-8888-4888-8888-888888888888',
            skill_id: SKILL,
            canonical_name: 'Writing for loops',
            status: 'ACCEPTED',
            confidence: 0.9,
            evidence_span: 'for name in names',
            attribution_status: 'ATTRIBUTED',
            actor: 'STUDENT',
            attributed_actor: 'STUDENT',
            qualification_reason: 'QUALIFIED',
            attribution_confidence: 0.95,
            evidence_decision: 'EVIDENCE_CREATED',
            evidence_id: '55555555-5555-4555-8555-555555555555',
            evidence_type: 'INDEPENDENT_APPLICATION',
            outcome_signal: 'CORRECT',
            excluded: false,
            exclusion_reason: null,
            correction: null,
          },
        ],
        evidence_count: 1,
        excluded_evidence_count: 0,
        correction: null,
      },
    ],
    ...overrides,
  };
}

export const SESSION = '77777777-7777-4777-8777-777777777777';

export function verificationSession(overrides: Partial<VerificationSessionSummary> = {}): VerificationSessionSummary {
  return {
    id: SESSION,
    skill_id: SKILL,
    canonical_name: 'Choosing LEFT JOIN',
    course_id: COURSE,
    state: 'READY',
    status: 'READY',
    trigger_type: 'VERIFY',
    reason_code: 'REPEATED_DELEGATION_UNVERIFIED',
    recommendation_id: '44444444-4444-4444-8444-444444444444',
    planned_difficulty: 0.9,
    assessment_type: 'mcq',
    estimated_minutes: 2,
    failure_code: null,
    abandon_reason: null,
    created_at: '2026-09-25T10:00:00Z',
    ready_at: '2026-09-25T10:00:30Z',
    started_at: null,
    submitted_at: null,
    evaluated_at: null,
    abandoned_at: null,
    result: null,
    ...overrides,
  };
}

export function verificationChallenge(overrides: Partial<VerificationChallenge> = {}): VerificationChallenge {
  return {
    session_id: SESSION,
    item_id: '88888888-8888-4888-8888-888888888888',
    skill_id: SKILL,
    canonical_name: 'Choosing LEFT JOIN',
    skill_description: 'Choose LEFT JOIN when unmatched rows must be kept.',
    assessment_type: 'mcq',
    prompt: 'Which join lists every member, including members without loans?',
    choices: [
      { key: 'A', text: 'INNER JOIN' },
      { key: 'B', text: 'LEFT JOIN from members' },
    ],
    multiple_select: false,
    estimated_minutes: 2,
    max_response_chars: 4000,
    ...overrides,
  };
}

export function verificationQueue(overrides: Partial<VerificationsResponse> = {}): VerificationsResponse {
  return {
    planner_version: 'verification-planner/p6-v1',
    budget: { day: '2026-09-25', timezone: 'UTC', daily_limit: 2, planned_today: 1, remaining_today: 1 },
    preparing: [],
    ready: [],
    in_progress: [],
    pending: [],
    completed: [],
    closed: [],
    ...overrides,
  };
}
