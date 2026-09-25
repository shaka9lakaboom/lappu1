-- pgTAP tests for migration 0007 (P5 feedback + recommendations).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(49);

-- Fixtures, written as the table owner (the way the backend writes) -------
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000007a1', 'p5-a@test.invalid'),
    ('00000000-0000-4000-8000-0000000007b1', 'p5-b@test.invalid');

insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status, source)
values
    ('70000000-0000-4000-8000-000000000001', 'p5t-left-join', 'P5T Choosing LEFT JOIN', 'p5t choosing left join',
     'Choose LEFT JOIN when unmatched rows must be kept.', 'SKILL', 'ACTIVE', 'SEED'),
    ('70000000-0000-4000-8000-000000000002', 'p5t-join-syntax', 'P5T JOIN Syntax', 'p5t join syntax',
     'Write syntactically valid SQL JOIN clauses.', 'SKILL', 'ACTIVE', 'SEED'),
    ('70000000-0000-4000-8000-000000000003', 'p5t-aliases', 'P5T Table Aliases', 'p5t table aliase',
     'Use table aliases to shorten qualified column names.', 'SKILL', 'ACTIVE', 'SEED');

insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version, input_hash, latency_ms, status)
values
    ('73000000-0000-4000-8000-000000000001', 'job:p5', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash',
     'turn-analysis/v1', repeat('1', 64), 100, 'SUCCEEDED'),
    ('73000000-0000-4000-8000-000000000002', 'job:p5', 'SKILL_ATTRIBUTION', 'google', 'gemini-3.7-flash',
     'skill-attribution/v1', repeat('2', 64), 100, 'SUCCEEDED');

insert into public.conversations (id, learner_id, source_provider, external_id, first_seen_at, last_seen_at)
values ('71000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000007a1', 'chatgpt', 'p5-conv-a', now(), now());
insert into public.raw_messages (
    id, learner_id, conversation_id, source_provider, source_method, external_message_id,
    external_parent_message_id, message_index, role, content_text, content_format, content_hash,
    fingerprint, revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id
) values
    ('72000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000007a1',
     '71000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p5-msg-a1', null, 0, 'user',
     'I need every customer, so LEFT JOIN orders o AS alias. How do I write it?', 'text', repeat('3', 64),
     repeat('7', 64), 0, now(), false, gen_random_uuid(), 'chatgpt:p5-msg-a1:r0'),
    ('72000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000007a1',
     '71000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p5-msg-a2', 'p5-msg-a1', 1,
     'assistant', 'Right. SELECT c.name FROM customers c LEFT JOIN orders o ON ...', 'text', repeat('4', 64),
     repeat('8', 64), 0, now(), false, gen_random_uuid(), 'chatgpt:p5-msg-a2:r0');

insert into public.activity_segments (
    id, learner_id, conversation_id, anchor_message_id, user_message_id, assistant_message_id,
    source_message_ids, segment_index, segment_count, text, context, intent, learning_relevance,
    relevance_confidence, skill_bearing, skill_bearing_confidence, reason_code, route, route_reason,
    context_incomplete, qualification_model_run_id, prompt_version, analysis_version
) values (
    '74000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000007a1',
    '71000000-0000-4000-8000-0000000000a1', '72000000-0000-4000-8000-0000000000a1',
    '72000000-0000-4000-8000-0000000000a1', '72000000-0000-4000-8000-0000000000a2',
    array['72000000-0000-4000-8000-0000000000a1', '72000000-0000-4000-8000-0000000000a2']::uuid[],
    0, 1, 'LEFT JOIN reasoning + syntax', 'academic', 'solve', 'high', 0.9, true, 0.9, 'CODE_REQUEST',
    'MAP', 'LEARNING_SKILL_BEARING', false, '73000000-0000-4000-8000-000000000001', 'turn-analysis/v1', 'p3a-v1'
);
insert into public.mapping_decisions (
    id, segment_id, learner_id, outcome, retrieval_candidates, mapper_candidate_ids,
    mapping_model_run_id, prompt_versions, policy_snapshot, mapper_version
) values (
    '75000000-0000-4000-8000-000000000001', '74000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-0000000007a1', 'MAPPED', '[]'::jsonb,
    array['70000000-0000-4000-8000-000000000001', '70000000-0000-4000-8000-000000000002',
          '70000000-0000-4000-8000-000000000003']::uuid[],
    '73000000-0000-4000-8000-000000000001', '{}'::jsonb, '{}'::jsonb, 'mapper/p3a-turn-v1'
);
insert into public.skill_mappings (
    id, decision_id, segment_id, learner_id, skill_id, status, confidence, first_pass_confidence,
    reason_code, status_reason, evidence_span, mapper_version
)
select ('76000000-0000-4000-8000-00000000000' || n)::uuid, '75000000-0000-4000-8000-000000000001',
       '74000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000007a1',
       ('70000000-0000-4000-8000-00000000000' || n)::uuid, 'ACCEPTED', 0.91, 0.91, 'REASONING',
       'FIRST_PASS_ACCEPTED', 'so LEFT JOIN orders', 'mapper/p3a-turn-v1'
  from generate_series(1, 3) as n;
insert into public.attributions (
    id, learner_id, mapping_id, decision_id, segment_id, skill_id, status, actor, confidence,
    student_span, proposed_evidence_type, outcome_signal, rationale_code, evidence_decision,
    model_run_id, prompt_version, attributor_version, attribution_version)
select ('77000000-0000-4000-8000-00000000000' || n)::uuid, '00000000-0000-4000-8000-0000000007a1',
       ('76000000-0000-4000-8000-00000000000' || n)::uuid, '75000000-0000-4000-8000-000000000001',
       '74000000-0000-4000-8000-000000000001', ('70000000-0000-4000-8000-00000000000' || n)::uuid,
       'ATTRIBUTED', 'STUDENT', 0.9, 'so LEFT JOIN orders', 'INDEPENDENT_EXPLANATION', 'CORRECT',
       'STUDENT_EXPLAINED_REASONING', 'EVIDENCE_CREATED', '73000000-0000-4000-8000-000000000002',
       'skill-attribution/v1', 'attributor/p3b-v1', 'p3b-v1'
  from generate_series(1, 3) as n;

-- Captured-activity evidence of attribution n (skill n, mapping n), as the backend writes it.
create function pg_temp.evidence(p_id uuid, n int)
returns void language sql as $$
    insert into public.evidence_events (
        id, learner_id, skill_id, source_type, source_id, attribution_id, mapping_id, decision_id, segment_id,
        raw_message_ids, evidence_type, actor, outcome_signal, outcome, difficulty, difficulty_multiplier,
        independence, base_weight, strength, mapping_confidence, attribution_confidence,
        evidence_confidence, evidence_span, model_run_ids, qualification_reason, qualifier_version,
        policy_snapshot, occurred_at)
    values (p_id, '00000000-0000-4000-8000-0000000007a1', ('70000000-0000-4000-8000-00000000000' || n)::uuid,
            'AI_ACTIVITY', ('77000000-0000-4000-8000-00000000000' || n)::uuid,
            ('77000000-0000-4000-8000-00000000000' || n)::uuid, ('76000000-0000-4000-8000-00000000000' || n)::uuid,
            '75000000-0000-4000-8000-000000000001', '74000000-0000-4000-8000-000000000001',
            array['72000000-0000-4000-8000-0000000000a1', '72000000-0000-4000-8000-0000000000a2']::uuid[],
            'INDEPENDENT_EXPLANATION', 'STUDENT', 'CORRECT', 1, 0.5, 1.0, 0.8, 0.75, 0.54, 0.91, 0.9, 0.9,
            '{"student": "so LEFT JOIN orders", "ai": null, "mapping": "so LEFT JOIN orders"}'::jsonb,
            array['73000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000002']::uuid[],
            'QUALIFIED', 'evidence/p3b-v1', '{}'::jsonb, now());
$$;

select pg_temp.evidence('78000000-0000-4000-8000-000000000001', 1);

-- The graded verification behind the verification evidence below (migration 0008 requires
-- VERIFICATION evidence to name its result): session -> item -> result.
insert into public.verification_sessions (id, learner_id, skill_id, trigger_type, reason_code, planned_difficulty,
                                          difficulty_min, difficulty_max, plan_day, plan_timezone, planner_version,
                                          planning_inputs)
values ('79100000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000007a1',
        '70000000-0000-4000-8000-000000000001', 'VERIFY', 'REPEATED_DELEGATION_UNVERIFIED', 0.5, 0.35, 0.65,
        current_date, 'UTC', 'verification-planner/test-v1', '{}');
insert into public.verification_items (id, session_id, learner_id, skill_id, assessment_type, grader_type, prompt,
                                       expected_answer, rubric, difficulty, transfer_distance, estimated_minutes,
                                       generator_version, prompt_version, generation_model_run_id, generation_attempt,
                                       prompt_fingerprint, validator_version, validation)
values ('79200000-0000-4000-8000-000000000001', '79100000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-0000000007a1', '70000000-0000-4000-8000-000000000001', 'short_response',
        'RUBRIC_AI', 'Explain when a LEFT JOIN keeps rows that an INNER JOIN drops.', 'Unmatched left rows.',
        '[{"criterion": "Names the unmatched rows", "points": 1}]', 0.5, 'medium', 3,
        'verification-generator/test-v1', 'verification-generation/v1', '73000000-0000-4000-8000-000000000002', 1,
        repeat('f', 64), 'verification-validator/test-v1', '{}');
update public.verification_sessions set state = 'READY', ready_at = now()
 where id = '79100000-0000-4000-8000-000000000001';
update public.verification_sessions set state = 'IN_PROGRESS', started_at = now()
 where id = '79100000-0000-4000-8000-000000000001';
update public.verification_sessions
   set state = 'SUBMITTED', submitted_at = now(), submitted_response = '{"answer": "graded answer"}',
       submission_idempotency_key = 'k-verification', submission_request_hash = repeat('c', 64)
 where id = '79100000-0000-4000-8000-000000000001';
insert into public.verification_results (id, item_id, session_id, learner_id, skill_id, response, score, pass,
                                         outcome_signal, outcome, evaluation, feedback, grading_confidence,
                                         evaluator_type, evaluator_version, evaluator_model_run_id,
                                         evaluator_prompt_version, policy_snapshot)
values ('79000000-0000-4000-8000-000000000001', '79200000-0000-4000-8000-000000000001',
        '79100000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000007a1',
        '70000000-0000-4000-8000-000000000001', '{"answer": "graded answer"}', 1, true, 'CORRECT', 1, '{}',
        'Correct.', 0.85, 'AI_RUBRIC', 'evaluator/test-v1', '73000000-0000-4000-8000-000000000002',
        'verification-evaluation/v1', '{}');
update public.verification_sessions set state = 'EVALUATED', evaluated_at = now()
 where id = '79100000-0000-4000-8000-000000000001';

-- Verification evidence (P6 shape): not captured activity.
insert into public.evidence_events (
    id, learner_id, skill_id, source_type, source_id, raw_message_ids, evidence_type, actor, outcome_signal,
    outcome, difficulty, difficulty_multiplier, independence, base_weight, strength, mapping_confidence,
    attribution_confidence, grading_confidence, evidence_confidence, evidence_span, model_run_ids,
    qualification_reason, qualifier_version, policy_snapshot, occurred_at)
values ('78000000-0000-4000-8000-000000000009', '00000000-0000-4000-8000-0000000007a1',
        '70000000-0000-4000-8000-000000000001', 'VERIFICATION', '79000000-0000-4000-8000-000000000001', '{}',
        'VERIFICATION', 'STUDENT', 'CORRECT', 1, 0.5, 1.0, 1.0, 1.5, 1.275, 1.0, 1.0, 0.85, 0.85,
        '{"response": "graded answer"}'::jsonb, '{}'::uuid[], 'GRADED', 'verification/test-v1', '{}'::jsonb, now());

-- A feedback row as the backend writes it (skill/mapping/segment are resolved by the guard).
create function pg_temp.feedback(
    p_key text, p_action text, p_target_type text, p_target uuid,
    p_excluded uuid[] default '{}', p_verdict text default null, p_note text default null,
    p_user uuid default '00000000-0000-4000-8000-0000000007a1', p_skill uuid default null,
    p_recomputed uuid[] default '{}')
returns void language sql as $$
    insert into public.feedback (user_id, target_type, target_id, action, verdict, note, skill_id,
                                 excluded_evidence_ids, recomputed_skill_ids, client_request_id, request_hash)
    values (p_user, p_target_type::public.feedback_target_type, p_target, p_action::public.feedback_action,
            p_verdict::public.feedback_verdict, p_note, p_skill, p_excluded, p_recomputed, p_key, repeat('a', 64));
$$;

-- A recommendation as the deterministic refresh writes it.
create function pg_temp.recommend(
    p_type text, p_state text default 'UNKNOWN', p_priority int default 0, p_reason text default 'NOT_ENOUGH_EVIDENCE',
    p_related uuid default null, p_skill uuid default '70000000-0000-4000-8000-000000000001')
returns uuid language sql as $$
    insert into public.recommendations (learner_id, skill_id, type, priority, reason_code, related_skill_id,
                                        mastery_state, inputs, algorithm_version)
    values ('00000000-0000-4000-8000-0000000007a1', p_skill, p_type::public.recommendation_type, p_priority,
            p_reason, p_related, p_state::public.mastery_state, '{}'::jsonb, 'recommendations/p5-v1')
    returning id;
$$;

-- Schema ------------------------------------------------------------------
select has_table('public', 'feedback', 'feedback exists');
select has_table('public', 'recommendations', 'recommendations exists');
select enum_has_labels('public', 'feedback_action', array['WRONG_SKILL', 'DONT_COUNT', 'EVALUATION'],
    'feedback_action labels');
select enum_has_labels('public', 'feedback_target_type',
    array['EVIDENCE_EVENT', 'SKILL_MAPPING', 'ACTIVITY_SEGMENT', 'SKILL', 'RECOMMENDATION'],
    'feedback_target_type labels');
select enum_has_labels('public', 'recommendation_type',
    array['NO_ACTION', 'PRACTICE', 'VERIFY', 'PREREQUISITE', 'REVERIFY'], 'recommendation_type labels (Engine 16)');
select enum_has_labels('public', 'recommendation_state', array['ACTIVE', 'SUPERSEDED', 'COMPLETED', 'DISMISSED'],
    'recommendation_state labels');
select ok(
    (select bool_and(c.relrowsecurity) from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname in ('feedback', 'recommendations')),
    'RLS is enabled on feedback and recommendations'
);
select ok(
    (select (value ->> 'max_active_verify')::int = 2
            and (value -> 'debt_bands' ->> 'moderate_min')::float8 = 15
            and value -> 'prerequisite_gap_states' = '["EMERGING"]'::jsonb
       from public.policy_config where key = 'recommendations' and scope_type = 'global'),
    'recommendations policy is seeded (2 verification recommendations, debt bands)'
);

-- Feedback guard ----------------------------------------------------------
select throws_ok(
    $$ select pg_temp.feedback('k-other', 'DONT_COUNT', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               p_user => '00000000-0000-4000-8000-0000000007b1') $$,
    '23503', null, 'feedback cannot target another learner''s evidence');
select throws_ok(
    $$ select pg_temp.feedback('k-bad', 'WRONG_SKILL', 'ACTIVITY_SEGMENT', '74000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'WRONG_SKILL concerns a mapping, not a whole task unit');
select throws_ok(
    $$ select pg_temp.feedback('k-empty', 'EVALUATION', 'SKILL', '70000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'an evaluation carries a verdict or a note');
select throws_ok(
    $$ select pg_temp.feedback('k-lie', 'DONT_COUNT', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               array['78000000-0000-4000-8000-000000000001']::uuid[]) $$,
    '23514', null, 'a correction cannot claim evidence it did not exclude');
select throws_ok(
    $$ select pg_temp.feedback('k-verif', 'DONT_COUNT', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000009') $$,
    '23514', null, 'verification evidence is not captured activity and cannot be corrected');

-- DONT_COUNT: one-way exclusion, then the feedback records its effect.
update public.evidence_events set excluded = true, exclusion_reason = 'LEARNER_DONT_COUNT', excluded_at = now()
 where id = '78000000-0000-4000-8000-000000000001';
select lives_ok(
    $$ select pg_temp.feedback('k-1', 'DONT_COUNT', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               array['78000000-0000-4000-8000-000000000001']::uuid[],
                               p_skill => '70000000-0000-4000-8000-000000000003',
                               p_recomputed => array['70000000-0000-4000-8000-000000000001']::uuid[]) $$,
    'DONT_COUNT records the evidence it excluded');
select results_eq(
    $$ select skill_id, mapping_id, segment_id from public.feedback where client_request_id = 'k-1' $$,
    $$ values ('70000000-0000-4000-8000-000000000001'::uuid, '76000000-0000-4000-8000-000000000001'::uuid,
               '74000000-0000-4000-8000-000000000001'::uuid) $$,
    'the skill, mapping and segment are copied from the target, not taken from the client');
select throws_ok(
    $$ select pg_temp.feedback('k-1', 'EVALUATION', 'SKILL', '70000000-0000-4000-8000-000000000001',
                               p_verdict => 'AGREE') $$,
    '23505', null, 'an idempotency key is used once per learner');
select throws_ok(
    $$ select pg_temp.feedback('k-2', 'DONT_COUNT', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'the same correction of the same target is never recorded twice');
select lives_ok(
    $$ select pg_temp.feedback('k-3', 'EVALUATION', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               p_verdict => 'DISAGREE', p_note => 'This was my friend''s code.');
       select pg_temp.feedback('k-4', 'EVALUATION', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               p_verdict => 'UNCLEAR') $$,
    'evaluations may be repeated (each with its own key)');
select throws_ok(
    $$ select pg_temp.feedback('k-5', 'EVALUATION', 'EVIDENCE_EVENT', '78000000-0000-4000-8000-000000000001',
                               array['78000000-0000-4000-8000-000000000001']::uuid[], p_verdict => 'AGREE') $$,
    '23514', null, 'an evaluation never changes evidence');
select throws_ok(
    $$ update public.feedback set note = 'changed' where client_request_id = 'k-3' $$,
    '55000', null, 'feedback is append-only');

-- Evidence exclusion stays one-way; provenance is never deleted --------------
select throws_ok(
    $$ update public.evidence_events set excluded = false, exclusion_reason = null, excluded_at = null
        where id = '78000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'an exclusion cannot be undone');
select results_eq(
    $$ select (select count(*) from public.raw_messages where learner_id = '00000000-0000-4000-8000-0000000007a1'),
              (select count(*) from public.activity_segments where learner_id = '00000000-0000-4000-8000-0000000007a1'),
              (select count(*) from public.mapping_decisions where learner_id = '00000000-0000-4000-8000-0000000007a1'),
              (select count(*) from public.skill_mappings where learner_id = '00000000-0000-4000-8000-0000000007a1'),
              (select count(*) from public.attributions where learner_id = '00000000-0000-4000-8000-0000000007a1'),
              (select count(*) from public.evidence_events where learner_id = '00000000-0000-4000-8000-0000000007a1') $$,
    $$ values (2::bigint, 1::bigint, 1::bigint, 3::bigint, 3::bigint, 2::bigint) $$,
    'the corrected activity keeps its whole provenance chain');

-- A correction also holds for evidence written later (e.g. a deferred job).
select lives_ok(
    $$ select pg_temp.feedback('k-6', 'WRONG_SKILL', 'SKILL_MAPPING', '76000000-0000-4000-8000-000000000002') $$,
    'WRONG_SKILL of a mapping without evidence yet is recorded');
select pg_temp.evidence('78000000-0000-4000-8000-000000000002', 2);
select results_eq(
    $$ select excluded, exclusion_reason from public.evidence_events where id = '78000000-0000-4000-8000-000000000002' $$,
    $$ values (true, 'LEARNER_WRONG_SKILL') $$,
    'evidence written later from a wrong-skill mapping is born excluded');
select lives_ok(
    $$ select pg_temp.feedback('k-7', 'DONT_COUNT', 'ACTIVITY_SEGMENT', '74000000-0000-4000-8000-000000000001') $$,
    'DONT_COUNT of a whole task unit is recorded');
select pg_temp.evidence('78000000-0000-4000-8000-000000000003', 3);
select results_eq(
    $$ select excluded, exclusion_reason from public.evidence_events where id = '78000000-0000-4000-8000-000000000003' $$,
    $$ values (true, 'LEARNER_DONT_COUNT') $$,
    'evidence written later from a task unit the learner did not want counted is born excluded');
select is(
    (select excluded from public.evidence_events where id = '78000000-0000-4000-8000-000000000009'),
    false, 'corrections never touch evidence from other sources');

-- Recommendations ---------------------------------------------------------
select lives_ok($$ select pg_temp.recommend('NO_ACTION') $$, 'an UNKNOWN skill gets NO_ACTION');
select throws_ok($$ select pg_temp.recommend('NO_ACTION') $$, '23505', null,
    'at most one ACTIVE recommendation per learner and skill');
select throws_ok(
    $$ select pg_temp.recommend('PRACTICE', 'UNKNOWN', 40, 'EMERGING_NEEDS_PRACTICE',
                                p_skill => '70000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'unknown is not weak: no practice without evidence');
select throws_ok(
    $$ select pg_temp.recommend('PREREQUISITE', 'EMERGING', 50, 'PREREQUISITE_GAP',
                                p_skill => '70000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'PREREQUISITE names the prerequisite skill');
select throws_ok(
    $$ select pg_temp.recommend('NO_ACTION', 'DEMONSTRATED', 10, 'INDEPENDENT_EVIDENCE_SUFFICIENT',
                                p_skill => '70000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'NO_ACTION has priority 0');
select throws_ok(
    $$ select pg_temp.recommend('REVERIFY', 'DEVELOPING', 85, 'VERIFICATION_STALE',
                                p_skill => '70000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'REVERIFY only for NEEDS_REVERIFICATION');
select lives_ok(
    $$ select pg_temp.recommend('PREREQUISITE', 'DEVELOPING', 55, 'PREREQUISITE_GAP',
                                '70000000-0000-4000-8000-000000000003', '70000000-0000-4000-8000-000000000002') $$,
    'PREREQUISITE of a developing skill names its weak prerequisite');
select lives_ok(
    $$ update public.recommendations set priority = 57, inputs = '{"importance": 0.7}'::jsonb
        where skill_id = '70000000-0000-4000-8000-000000000002' and state = 'ACTIVE' $$,
    'a refresh may update the priority and inputs of an ACTIVE recommendation');
select throws_ok(
    $$ update public.recommendations set type = 'VERIFY', related_skill_id = null
        where skill_id = '70000000-0000-4000-8000-000000000002' and state = 'ACTIVE' $$,
    '55000', null, 'an action is never rewritten in place');
select throws_ok(
    $$ update public.recommendations set state = 'SUPERSEDED'
        where skill_id = '70000000-0000-4000-8000-000000000002' and state = 'ACTIVE' $$,
    '23514', null, 'a resolved recommendation records when it was resolved');
select lives_ok(
    $$ update public.recommendations set state = 'SUPERSEDED', resolved_at = now()
        where skill_id = '70000000-0000-4000-8000-000000000002' and state = 'ACTIVE';
       select pg_temp.recommend('PRACTICE', 'DEVELOPING', 37, 'DEVELOPING_NEEDS_PRACTICE',
                                p_skill => '70000000-0000-4000-8000-000000000002') $$,
    'a changed action supersedes the old row and starts a new one');
select throws_ok(
    $$ update public.recommendations set state = 'ACTIVE', resolved_at = null
        where skill_id = '70000000-0000-4000-8000-000000000002' and state = 'SUPERSEDED' $$,
    '55000', null, 'a resolved recommendation is final');

-- Row Level Security --------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000007a1", "role": "authenticated"}';
select is((select count(*)::int from public.feedback), 5, 'a learner reads their own feedback');
select is((select count(*)::int from public.recommendations), 3, 'a learner reads their own recommendations');
select throws_ok(
    $$ insert into public.feedback (user_id, target_type, target_id, action, client_request_id, request_hash)
       values ('00000000-0000-4000-8000-0000000007a1', 'SKILL_MAPPING', '76000000-0000-4000-8000-000000000003',
               'WRONG_SKILL', 'k-client', repeat('b', 64)) $$,
    '42501', null, 'clients cannot write feedback directly (only through the backend)');
select throws_ok(
    $$ update public.recommendations set priority = 100 $$,
    '42501', null, 'clients cannot modify recommendations');
select throws_ok(
    $$ update public.evidence_events set excluded = true, exclusion_reason = 'CLIENT', excluded_at = now() $$,
    '42501', null, 'clients cannot exclude evidence directly');
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000007b1", "role": "authenticated"}';
select is((select count(*)::int from public.feedback), 0, 'another learner sees none of the feedback');
select is((select count(*)::int from public.recommendations), 0, 'another learner sees none of the recommendations');
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.feedback $$, '42501', null, 'anon has no access to feedback');
select throws_ok($$ select count(*) from public.recommendations $$, '42501', null,
    'anon has no access to recommendations');
reset role;
select ok(
    not has_function_privilege('authenticated', 'public.feedback_guard()', 'execute')
    and not has_function_privilege('authenticated', 'public.evidence_events_apply_corrections()', 'execute')
    and not has_function_privilege('authenticated', 'public.recommendations_guard_update()', 'execute')
    and not has_function_privilege('anon', 'public.feedback_guard()', 'execute'),
    'the P5 trigger functions are not executable by clients');

select * from finish();
rollback;
