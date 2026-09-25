-- pgTAP tests for migration 0005 (P3B attribution + immutable EvidenceEvents).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(40);

-- Fixtures, written as the table owner (the way the backend writes) -------
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000005a1', 'p3b-a@test.invalid'),
    ('00000000-0000-4000-8000-0000000005b1', 'p3b-b@test.invalid');

insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status, source)
values
    ('50000000-0000-4000-8000-000000000001', 'p3bt-left-join', 'P3BT Choosing LEFT JOIN', 'p3bt choosing left join',
     'Choose LEFT JOIN when unmatched rows must be kept.', 'SKILL', 'ACTIVE', 'SEED'),
    ('50000000-0000-4000-8000-000000000002', 'p3bt-join-syntax', 'P3BT JOIN Syntax', 'p3bt join syntax',
     'Write syntactically valid SQL JOIN clauses.', 'SKILL', 'ACTIVE', 'SEED');

insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version, input_hash, latency_ms, status)
values
    ('53000000-0000-4000-8000-000000000001', 'job:p3b', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash',
     'turn-analysis/v1', repeat('1', 64), 100, 'SUCCEEDED'),
    ('53000000-0000-4000-8000-000000000002', 'job:p3b', 'SKILL_ATTRIBUTION', 'google', 'gemini-3.7-flash',
     'skill-attribution/v1', repeat('2', 64), 100, 'SUCCEEDED');

insert into public.conversations (id, learner_id, source_provider, external_id, first_seen_at, last_seen_at)
values ('51000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000005a1', 'chatgpt', 'p3b-conv-a', now(), now());
insert into public.raw_messages (
    id, learner_id, conversation_id, source_provider, source_method, external_message_id,
    external_parent_message_id, message_index, role, content_text, content_format, content_hash,
    fingerprint, revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id
) values
    ('52000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000005a1',
     '51000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p3b-msg-a1', null, 0, 'user',
     'I need every customer, so LEFT JOIN orders. How do I write it?', 'text', repeat('3', 64), repeat('7', 64),
     0, now(), false, gen_random_uuid(), 'chatgpt:p3b-msg-a1:r0'),
    ('52000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000005a1',
     '51000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p3b-msg-a2', 'p3b-msg-a1', 1,
     'assistant', 'Right. SELECT c.name FROM customers c LEFT JOIN orders o ON ...', 'text', repeat('4', 64),
     repeat('8', 64), 0, now(), false, gen_random_uuid(), 'chatgpt:p3b-msg-a2:r0');

insert into public.activity_segments (
    id, learner_id, conversation_id, anchor_message_id, user_message_id, assistant_message_id,
    source_message_ids, segment_index, segment_count, text, context, intent, learning_relevance,
    relevance_confidence, skill_bearing, skill_bearing_confidence, reason_code, route, route_reason,
    context_incomplete, qualification_model_run_id, prompt_version, analysis_version
) values (
    '54000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000005a1',
    '51000000-0000-4000-8000-0000000000a1', '52000000-0000-4000-8000-0000000000a1',
    '52000000-0000-4000-8000-0000000000a1', '52000000-0000-4000-8000-0000000000a2',
    array['52000000-0000-4000-8000-0000000000a1', '52000000-0000-4000-8000-0000000000a2']::uuid[],
    0, 1, 'LEFT JOIN reasoning + syntax', 'academic', 'solve', 'high', 0.9, true, 0.9, 'CODE_REQUEST',
    'MAP', 'LEARNING_SKILL_BEARING', false, '53000000-0000-4000-8000-000000000001', 'turn-analysis/v1', 'p3a-v1'
);
insert into public.mapping_decisions (
    id, segment_id, learner_id, outcome, retrieval_candidates, mapper_candidate_ids,
    mapping_model_run_id, prompt_versions, policy_snapshot, mapper_version
) values (
    '55000000-0000-4000-8000-000000000001', '54000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-0000000005a1', 'MAPPED', '[]'::jsonb,
    array['50000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000002']::uuid[],
    '53000000-0000-4000-8000-000000000001', '{}'::jsonb, '{}'::jsonb, 'mapper/p3a-turn-v1'
);
insert into public.skill_mappings (
    id, decision_id, segment_id, learner_id, skill_id, status, confidence, first_pass_confidence,
    reason_code, status_reason, evidence_span, mapper_version
) values
    ('56000000-0000-4000-8000-000000000001', '55000000-0000-4000-8000-000000000001',
     '54000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000005a1',
     '50000000-0000-4000-8000-000000000001', 'ACCEPTED', 0.91, 0.91, 'REASONING', 'FIRST_PASS_ACCEPTED',
     'so LEFT JOIN orders', 'mapper/p3a-turn-v1'),
    ('56000000-0000-4000-8000-000000000002', '55000000-0000-4000-8000-000000000001',
     '54000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000005a1',
     '50000000-0000-4000-8000-000000000002', 'ABSTAINED', 0.5, 0.5, 'IMPLEMENTATION', 'LOW_CONFIDENCE',
     null, 'mapper/p3a-turn-v1');

-- An attribution of the ACCEPTED mapping, as the backend writes it.
create function pg_temp.attribute(p_id uuid, p_mapping uuid, p_skill uuid, p_status text, p_actor text, p_version text)
returns void language sql as $$
    insert into public.attributions (
        id, learner_id, mapping_id, decision_id, segment_id, skill_id, status, actor, confidence,
        student_span, proposed_evidence_type, outcome_signal, rationale_code, evidence_decision,
        model_run_id, prompt_version, attributor_version, attribution_version)
    values (p_id, '00000000-0000-4000-8000-0000000005a1', p_mapping, '55000000-0000-4000-8000-000000000001',
            '54000000-0000-4000-8000-000000000001', p_skill, p_status::public.attribution_status,
            p_actor::public.evidence_actor, case when p_actor is null then null else 0.9 end,
            case when p_actor is null then null else 'so LEFT JOIN orders' end,
            case when p_actor is null then null else 'INDEPENDENT_EXPLANATION' end,
            case when p_actor is null then null else 'CORRECT'::public.outcome_signal end,
            'STUDENT_EXPLAINED_REASONING',
            case when p_actor is null then 'MODEL_OUTPUT_INVALID' else 'EVIDENCE_CREATED' end,
            '53000000-0000-4000-8000-000000000002', 'skill-attribution/v1', 'attributor/p3b-v1', p_version);
$$;

-- Evidence of that attribution, with the fields a guard or check must reject overridable.
create function pg_temp.evidence(
    p_type text default 'INDEPENDENT_EXPLANATION', p_actor text default 'STUDENT',
    p_signal text default 'CORRECT', p_outcome float8 default 1, p_strength float8 default 0.54,
    p_source text default 'AI_ACTIVITY', p_mapping_confidence real default 0.91,
    p_evidence_confidence real default 0.9, p_grading real default null,
    p_raw uuid[] default array['52000000-0000-4000-8000-0000000000a1', '52000000-0000-4000-8000-0000000000a2']::uuid[])
returns void language sql as $$
    insert into public.evidence_events (
        learner_id, skill_id, source_type, source_id, attribution_id, mapping_id, decision_id, segment_id,
        raw_message_ids, evidence_type, actor, outcome_signal, outcome, difficulty, difficulty_multiplier,
        independence, base_weight, strength, mapping_confidence, attribution_confidence, grading_confidence,
        evidence_confidence, evidence_span, model_run_ids, qualification_reason, qualifier_version,
        policy_snapshot, occurred_at)
    values ('00000000-0000-4000-8000-0000000005a1', '50000000-0000-4000-8000-000000000001',
            p_source::public.evidence_source_type, '57000000-0000-4000-8000-000000000001',
            '57000000-0000-4000-8000-000000000001', '56000000-0000-4000-8000-000000000001',
            '55000000-0000-4000-8000-000000000001', '54000000-0000-4000-8000-000000000001', p_raw,
            p_type::public.evidence_type, p_actor::public.evidence_actor, p_signal::public.outcome_signal,
            p_outcome, 0.5, 1.0, 0.8, 0.75, p_strength, p_mapping_confidence, 0.9, p_grading,
            p_evidence_confidence,
            '{"student": "so LEFT JOIN orders", "ai": null, "mapping": "so LEFT JOIN orders"}'::jsonb,
            array['53000000-0000-4000-8000-000000000001', '53000000-0000-4000-8000-000000000002']::uuid[],
            'QUALIFIED', 'evidence/p3b-v1', '{}'::jsonb, now());
$$;

-- Evidence from a non-activity source (P6 verification, P7 teacher / assessment).
create function pg_temp.other_evidence(
    p_source text, p_type text, p_source_id uuid,
    p_attribution uuid default null, p_raw uuid[] default '{}',
    p_grading real default 0.85, p_evidence_confidence real default 0.85)
returns void language sql as $$
    insert into public.evidence_events (
        learner_id, skill_id, source_type, source_id, attribution_id, raw_message_ids, evidence_type,
        actor, outcome_signal, outcome, difficulty, difficulty_multiplier, independence, base_weight,
        strength, mapping_confidence, attribution_confidence, grading_confidence, evidence_confidence,
        evidence_span, model_run_ids, qualification_reason, qualifier_version, policy_snapshot, occurred_at)
    values ('00000000-0000-4000-8000-0000000005a1', '50000000-0000-4000-8000-000000000001',
            p_source::public.evidence_source_type, p_source_id, p_attribution, p_raw,
            p_type::public.evidence_type, 'STUDENT', 'CORRECT', 1, 0.5, 1.0, 1.0, 1.5, 1.275,
            1.0, 1.0, p_grading, p_evidence_confidence, '{"response": "graded answer"}'::jsonb,
            '{}'::uuid[], 'GRADED', 'verification/test-v1', '{}'::jsonb, now());
$$;

-- Schema ------------------------------------------------------------------
select has_table('public', 'attributions', 'attributions exists');
select has_table('public', 'evidence_events', 'evidence_events exists');
select enum_has_labels('public', 'evidence_actor', array['STUDENT', 'AI', 'SHARED', 'UNKNOWN'], 'evidence_actor labels');
select enum_has_labels('public', 'evidence_type',
    array['EXPOSURE', 'OBSERVATION', 'ASSISTED_ATTEMPT', 'INDEPENDENT_EXPLANATION', 'INDEPENDENT_APPLICATION',
          'TRANSFER', 'VERIFICATION', 'EXECUTION_RESULT', 'TEACHER_EVIDENCE'], 'evidence_type labels');
select enum_has_labels('public', 'outcome_signal', array['CORRECT', 'INCORRECT', 'PARTIAL', 'NOT_APPLICABLE'],
    'outcome_signal labels');
select ok(
    (select bool_and(c.relrowsecurity) from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname in ('attributions', 'evidence_events')),
    'RLS is enabled on attributions and evidence_events'
);
select ok(
    (select (value -> 'base_weights' ->> 'EXPOSURE')::float8 = 0
            and (value -> 'base_weights' ->> 'OBSERVATION')::float8 = 0
            and (value -> 'base_weights' ->> 'INDEPENDENT_APPLICATION')::float8 = 1.0
       from public.policy_config where key = 'evidence' and scope_type = 'global')
    and exists (select 1 from public.policy_config where key = 'attribution'
                 and (value ->> 'min_confidence')::float8 = 0.80),
    'evidence + attribution policy are seeded (exposure/observation weight 0)'
);

-- Attribution guard -------------------------------------------------------
select throws_ok(
    $$ select pg_temp.attribute('57000000-0000-4000-8000-000000000009', '56000000-0000-4000-8000-000000000002',
                                '50000000-0000-4000-8000-000000000002', 'ATTRIBUTED', 'STUDENT', 'p3b-v1') $$,
    '23514', null,
    'attribution refers only to an ACCEPTED skill mapping'
);
select throws_ok(
    $$ select pg_temp.attribute('57000000-0000-4000-8000-000000000009', '56000000-0000-4000-8000-000000000001',
                                '50000000-0000-4000-8000-000000000002', 'ATTRIBUTED', 'STUDENT', 'p3b-v1') $$,
    '23514', null,
    'attribution copies the mapping''s skill exactly'
);
select throws_ok(
    $$ insert into public.attributions (learner_id, mapping_id, decision_id, segment_id, skill_id, status, actor,
                                        rationale_code, evidence_decision, prompt_version, attributor_version,
                                        attribution_version)
       values ('00000000-0000-4000-8000-0000000005a1', '56000000-0000-4000-8000-000000000001',
               '55000000-0000-4000-8000-000000000001', '54000000-0000-4000-8000-000000000001',
               '50000000-0000-4000-8000-000000000001', 'ABSTAINED', 'STUDENT', 'MODEL_OUTPUT_INVALID',
               'MODEL_OUTPUT_INVALID', 'skill-attribution/v1', 'attributor/p3b-v1', 'p3b-v1') $$,
    '23514', null,
    'an abstained attribution carries no model judgement'
);
select lives_ok(
    $$ select pg_temp.attribute('57000000-0000-4000-8000-000000000001', '56000000-0000-4000-8000-000000000001',
                                '50000000-0000-4000-8000-000000000001', 'ATTRIBUTED', 'STUDENT', 'p3b-v1') $$,
    'an attribution of an ACCEPTED mapping is written'
);
select throws_ok(
    $$ select pg_temp.attribute('57000000-0000-4000-8000-000000000002', '56000000-0000-4000-8000-000000000001',
                                '50000000-0000-4000-8000-000000000001', 'ATTRIBUTED', 'STUDENT', 'p3b-v1') $$,
    '23505', null,
    'one attribution per mapping and attribution version (replay-safe)'
);

-- Evidence guards and checks -------------------------------------------
select lives_ok($$ select pg_temp.evidence() $$, 'qualified evidence with complete provenance is written');
select throws_ok($$ select pg_temp.evidence() $$, '23505', null, 'at most one evidence event per attribution (replay-safe)');
select throws_ok(
    $$ select pg_temp.evidence(p_type => 'EXPOSURE', p_signal => 'NOT_APPLICABLE', p_outcome => null, p_strength => 0.3) $$,
    '23514', null, 'EXPOSURE never carries strength');
select throws_ok(
    $$ select pg_temp.evidence(p_type => 'OBSERVATION', p_strength => 0) $$,
    '23514', null, 'OBSERVATION never carries an outcome');
select throws_ok(
    $$ select pg_temp.evidence(p_type => 'INDEPENDENT_APPLICATION', p_actor => 'AI') $$,
    '23514', null, 'an AI actor is never learner performance evidence');
select throws_ok(
    $$ select pg_temp.evidence(p_actor => 'UNKNOWN') $$,
    '23514', null, 'an UNKNOWN actor never creates evidence');
select throws_ok(
    $$ select pg_temp.evidence(p_type => 'VERIFICATION') $$,
    '23514', null, 'captured AI activity is never VERIFICATION evidence');
select throws_ok(
    $$ insert into public.evidence_events (
           learner_id, skill_id, source_type, source_id, raw_message_ids, evidence_type, actor, outcome_signal,
           outcome, difficulty, difficulty_multiplier, independence, base_weight, strength, mapping_confidence,
           attribution_confidence, evidence_confidence, evidence_span, qualification_reason, qualifier_version,
           policy_snapshot, occurred_at)
       values ('00000000-0000-4000-8000-0000000005a1', '50000000-0000-4000-8000-000000000001', 'AI_ACTIVITY',
               gen_random_uuid(), array['52000000-0000-4000-8000-0000000000a1']::uuid[], 'INDEPENDENT_APPLICATION',
               'STUDENT', 'CORRECT', 1, 0.5, 1.0, 1.0, 1.0, 0.9, 0.9, 0.9, 0.9, '{}'::jsonb, 'QUALIFIED',
               'evidence/p3b-v1', '{}'::jsonb, now()) $$,
    '23514', null, 'AI_ACTIVITY evidence always needs an attribution');
select throws_ok(
    $$ select pg_temp.evidence(p_grading => 0.85, p_evidence_confidence => 0.85) $$,
    '23514', null, 'captured AI activity carries no grading confidence');

-- Future sources stay structurally possible, without an attribution ---
select lives_ok(
    $$ select pg_temp.other_evidence('VERIFICATION', 'VERIFICATION', '58000000-0000-4000-8000-000000000001') $$,
    'P6 VERIFICATION evidence can be written without an attribution row');
select throws_ok(
    $$ select pg_temp.other_evidence('VERIFICATION', 'VERIFICATION', '58000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'every source is replay-safe: one event per source record and skill');
select lives_ok(
    $$ select pg_temp.other_evidence('TEACHER', 'TEACHER_EVIDENCE', '58000000-0000-4000-8000-000000000002',
                                     p_grading => null, p_evidence_confidence => 1.0) $$,
    'P7 TEACHER evidence can be written');
select throws_ok(
    $$ select pg_temp.other_evidence('VERIFICATION', 'VERIFICATION', '58000000-0000-4000-8000-000000000003',
                                     p_attribution => '57000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'non-activity evidence never claims an attribution');
select throws_ok(
    $$ select pg_temp.other_evidence('VERIFICATION', 'VERIFICATION', '58000000-0000-4000-8000-000000000004',
                                     p_raw => array['52000000-0000-4000-8000-0000000000a1']::uuid[]) $$,
    '23514', null, 'non-activity evidence never claims captured raw messages');
select throws_ok(
    $$ select pg_temp.other_evidence('TEACHER', 'VERIFICATION', '58000000-0000-4000-8000-000000000005') $$,
    '23514', null, 'only a SkillMirror verification is VERIFICATION evidence');
select throws_ok(
    $$ select pg_temp.other_evidence('VERIFICATION', 'VERIFICATION', '58000000-0000-4000-8000-000000000006',
                                     p_evidence_confidence => 1.0) $$,
    '23514', null, 'graded evidence confidence is min(mapping, attribution, grading)');
select throws_ok(
    $$ select pg_temp.evidence(p_signal => 'CORRECT', p_outcome => 0.5) $$,
    '23514', null, 'the outcome matches the outcome signal');
select throws_ok(
    $$ select pg_temp.evidence(p_evidence_confidence => 0.95) $$,
    '23514', null, 'evidence confidence is min(mapping, attribution)');
select throws_ok(
    $$ select pg_temp.evidence(p_mapping_confidence => 0.99, p_evidence_confidence => 0.9) $$,
    '23514', null, 'mapping confidence is copied from the skill mapping');
select throws_ok(
    $$ select pg_temp.evidence(p_raw => array['52000000-0000-4000-8000-0000000000a1']::uuid[]) $$,
    '23514', null, 'raw_message_ids are the segment''s source messages');

-- Immutability ------------------------------------------------------------
select throws_ok(
    $$ update public.evidence_events set strength = 2 where learner_id = '00000000-0000-4000-8000-0000000005a1' $$,
    '55000', null, 'evidence is append-only');
select lives_ok(
    $$ update public.evidence_events set excluded = true, exclusion_reason = 'LEARNER_FEEDBACK', excluded_at = now()
        where learner_id = '00000000-0000-4000-8000-0000000005a1' $$,
    'a one-way exclusion keeps provenance');
select throws_ok(
    $$ update public.evidence_events set excluded = false, exclusion_reason = null, excluded_at = null
        where learner_id = '00000000-0000-4000-8000-0000000005a1' $$,
    '55000', null, 'an exclusion cannot be undone in place');
select throws_ok(
    $$ update public.attributions set actor = 'AI' where learner_id = '00000000-0000-4000-8000-0000000005a1' $$,
    '55000', null, 'attributions are append-only');

-- RLS: learners read their own rows; clients never write ----------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000005a1", "role": "authenticated"}';
select is(
    (select count(*)::int from public.evidence_events) + (select count(*)::int from public.attributions),
    4, 'a learner reads their own attribution and evidence (activity, verification, teacher)');
select throws_ok(
    $$ insert into public.evidence_events (learner_id, skill_id) values
         ('00000000-0000-4000-8000-0000000005a1', '50000000-0000-4000-8000-000000000001') $$,
    '42501', null, 'clients cannot create evidence');
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000005b1", "role": "authenticated"}';
select is(
    (select count(*)::int from public.evidence_events) + (select count(*)::int from public.attributions),
    0, 'another learner sees none of it');
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.evidence_events $$, '42501', null, 'anon has no access to evidence');
reset role;

select * from finish();
rollback;
