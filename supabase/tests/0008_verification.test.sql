-- pgTAP tests for migration 0008 (P6 verification sessions, items, results, VERIFICATION
-- evidence and the VERIFIED / NEEDS_REVERIFICATION ledger states).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(79);

-- Fixtures, written as the table owner (the way the backend writes) -------
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000008a1', 'p6-a@test.invalid'),
    ('00000000-0000-4000-8000-0000000008b1', 'p6-b@test.invalid');

insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status,
                                difficulty_band, source)
values
    ('80000000-0000-4000-8000-000000000001', 'p6t-left-join', 'P6T Choosing LEFT JOIN', 'p6t choosing left join',
     'Choose LEFT JOIN when unmatched rows must be kept.', 'SKILL', 'ACTIVE', 3, 'SEED'),
    ('80000000-0000-4000-8000-000000000002', 'p6t-join-syntax', 'P6T JOIN Syntax', 'p6t join syntax',
     'Write syntactically valid SQL JOIN clauses.', 'SKILL', 'ACTIVE', 2, 'SEED'),
    ('80000000-0000-4000-8000-000000000003', 'p6t-aliases', 'P6T Table Aliases', 'p6t table aliase',
     'Use table aliases to shorten qualified column names.', 'SKILL', 'ACTIVE', 2, 'SEED');
-- JOIN syntax is a prerequisite of choosing LEFT JOIN.
insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source)
values ('80000000-0000-4000-8000-000000000002', '80000000-0000-4000-8000-000000000001', 'PREREQUISITE', 'SEED');

insert into public.courses (id, owner_id, name, graph_status, graph_version)
values
    ('81000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000008a1', 'P6T SQL', 'READY', 1),
    ('81000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000008b1', 'P6T SQL of B', 'READY', 1);
insert into public.course_memberships (course_id, user_id)
values
    ('81000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000008a1'),
    ('81000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000008b1');
insert into public.course_skills (course_id, skill_id, importance, source, graph_version)
select c, s, 0.8, 'SEED', 1
  from unnest(array['81000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000002']::uuid[]) c,
       unnest(array['80000000-0000-4000-8000-000000000001', '80000000-0000-4000-8000-000000000002',
                    '80000000-0000-4000-8000-000000000003']::uuid[]) s;

insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version, input_hash, latency_ms, status)
values
    ('83000000-0000-4000-8000-000000000001', 'job:p6', 'VERIFICATION_GENERATION', 'google', 'gemini-3.5-flash-lite',
     'verification-generation/v1', repeat('1', 64), 100, 'SUCCEEDED'),
    ('83000000-0000-4000-8000-000000000002', 'job:p6', 'VERIFICATION_EVALUATION', 'google', 'gemini-3.5-flash-lite',
     'verification-evaluation/v1', repeat('2', 64), 100, 'SUCCEEDED');

-- The P5 VERIFY recommendations the planner acts on (UNKNOWN + actionable debt).
insert into public.recommendations (id, learner_id, skill_id, type, priority, reason_code, mastery_state, inputs,
                                    algorithm_version)
values
    ('84000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000008a1',
     '80000000-0000-4000-8000-000000000001', 'VERIFY', 65, 'REPEATED_DELEGATION_UNVERIFIED', 'UNKNOWN', '{}',
     'recommendations/p6-v1'),
    ('84000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000008b1',
     '80000000-0000-4000-8000-000000000001', 'VERIFY', 65, 'REPEATED_DELEGATION_UNVERIFIED', 'UNKNOWN', '{}',
     'recommendations/p6-v1');

-- A PLANNED session as the deterministic planner writes it (band 0.35 - 0.65).
create function pg_temp.session(
    p_id uuid, p_skill uuid, p_rec uuid default null, p_course uuid default null,
    p_learner uuid default '00000000-0000-4000-8000-0000000008a1', p_state text default 'PLANNED',
    p_ready timestamptz default null)
returns void language sql as $$
    insert into public.verification_sessions (id, learner_id, course_id, skill_id, recommendation_id, trigger_type,
                                              reason_code, state, planned_difficulty, difficulty_min, difficulty_max,
                                              plan_day, plan_timezone, planner_version, planning_inputs, ready_at)
    values (p_id, p_learner, p_course, p_skill, p_rec, 'VERIFY', 'REPEATED_DELEGATION_UNVERIFIED',
            p_state::public.verification_state, 0.5, 0.35, 0.65, current_date, 'UTC',
            'verification-planner/p6-v1', '{"priority": 65}', p_ready);
$$;

-- A validated challenge item (the answer key stays in this server-only table).
create function pg_temp.item(
    p_id uuid, p_session uuid, p_skill uuid, p_type text default 'mcq', p_difficulty float8 default 0.5,
    p_prereqs uuid[] default '{}', p_choices jsonb default null,
    p_learner uuid default '00000000-0000-4000-8000-0000000008a1')
returns void language sql as $$
    insert into public.verification_items (id, session_id, learner_id, skill_id, assessment_type, grader_type,
                                           prompt, choices, expected_answer, rubric, difficulty, prerequisites_used,
                                           transfer_distance, estimated_minutes, generator_version, prompt_version,
                                           generation_model_run_id, generation_attempt, prompt_fingerprint,
                                           validator_version, validation)
    values (p_id, p_session, p_learner, p_skill, p_type::public.verification_assessment_type,
            case p_type when 'mcq' then 'MCQ_EXACT' when 'numeric' then 'NUMERIC_TOLERANCE'
                        else 'RUBRIC_AI' end::public.verification_grader_type,
            'A report must list every customer, including those without orders. Which join do you use?',
            coalesce(p_choices, case when p_type = 'mcq'
                                     then '[{"key": "A", "text": "INNER JOIN"}, {"key": "B", "text": "LEFT JOIN"}]'::jsonb
                                     else '[]'::jsonb end),
            case p_type when 'mcq' then 'B' when 'numeric' then '42' else 'Keep unmatched rows.' end,
            case when p_type in ('mcq', 'numeric') then '[]'::jsonb
                 else '[{"criterion": "Keeps unmatched rows", "points": 1}]'::jsonb end,
            p_difficulty, p_prereqs, 'medium', 3, 'verification-generator/p6-v1', 'verification-generation/v1',
            '83000000-0000-4000-8000-000000000001', 1, repeat('a', 64), 'verification-validator/p6-v1', '{}');
$$;

create function pg_temp.advance(p_session uuid, p_to text)
returns void language sql as $$
    update public.verification_sessions
       set state = p_to::public.verification_state,
           ready_at = case when p_to = 'READY' then now() else ready_at end,
           started_at = case when p_to = 'IN_PROGRESS' then now() else started_at end,
           evaluated_at = case when p_to = 'EVALUATED' then now() else evaluated_at end,
           abandoned_at = case when p_to = 'ABANDONED' then now() else abandoned_at end,
           abandon_reason = case when p_to = 'ABANDONED' then 'LEARNER_ABANDONED' else abandon_reason end
     where id = p_session;
$$;

create function pg_temp.submit(p_session uuid, p_key text, p_response jsonb default '{"selected": ["B"]}')
returns void language sql as $$
    update public.verification_sessions
       set state = 'SUBMITTED', submitted_at = now(), submitted_response = p_response,
           submission_idempotency_key = p_key, submission_request_hash = repeat('c', 64)
     where id = p_session;
$$;

-- A deterministic graded result (MCQ exact).
create function pg_temp.result(
    p_id uuid, p_item uuid, p_session uuid, p_response jsonb default '{"selected": ["B"]}',
    p_skill uuid default '80000000-0000-4000-8000-000000000001')
returns void language sql as $$
    insert into public.verification_results (id, item_id, session_id, learner_id, skill_id, response, score, pass,
                                             outcome_signal, outcome, evaluation, feedback, grading_confidence,
                                             evaluator_type, evaluator_version, policy_snapshot)
    values (p_id, p_item, p_session, '00000000-0000-4000-8000-0000000008a1', p_skill, p_response, 1, true,
            'CORRECT', 1, '{"selected": ["B"]}', 'Correct.', 1, 'DETERMINISTIC', 'grader/mcq-exact-v1', '{}');
$$;

-- VERIFICATION evidence of a result (source_type VERIFICATION, source_id = the result).
create function pg_temp.verification_evidence(
    p_result uuid, p_learner uuid default '00000000-0000-4000-8000-0000000008a1',
    p_signal text default 'CORRECT', p_outcome float8 default 1, p_mapping real default 1)
returns void language sql as $$
    insert into public.evidence_events (learner_id, skill_id, source_type, source_id, raw_message_ids, evidence_type,
                                        actor, outcome_signal, outcome, difficulty, difficulty_multiplier,
                                        independence, base_weight, strength, mapping_confidence,
                                        attribution_confidence, grading_confidence, evidence_confidence,
                                        evidence_span, model_run_ids, qualification_reason, qualifier_version,
                                        policy_snapshot, occurred_at)
    values (p_learner, '80000000-0000-4000-8000-000000000001', 'VERIFICATION', p_result, '{}', 'VERIFICATION',
            'STUDENT', p_signal::public.outcome_signal, p_outcome, 0.5, 1.0, 1.0, 1.5, 1.5 * p_mapping, p_mapping,
            1, 1, least(p_mapping, 1), '{"student": "B"}'::jsonb,
            array['83000000-0000-4000-8000-000000000001']::uuid[], 'VERIFICATION_PASSED', 'verification/p6-v1',
            '{}'::jsonb, now());
$$;

create function pg_temp.ledger(p_state text, p_learner uuid default '00000000-0000-4000-8000-0000000008a1',
                               p_skill uuid default '80000000-0000-4000-8000-000000000001')
returns void language sql as $$
    insert into public.skill_ledger as l (learner_id, skill_id, alpha, beta, mastery_mean, support, mastery_state,
                                          evidence_count, performance_evidence_count, recent_delegation_count,
                                          computed_as_of, algorithm_version, policy_snapshot)
    values (p_learner, p_skill, 5.3, 1.0, 5.3 / 6.3, 4.3, p_state::public.mastery_state, 5, 5, 0, now(),
            'ledger/p6-v1', '{}'::jsonb)
    on conflict (learner_id, skill_id) do update set mastery_state = excluded.mastery_state;
$$;

-- Schema: a clean reset applies 0001-0008 (later migrations follow; 0009 pins the full list);
-- 0001-0007 are the migrations on hosted -------------------------------------------------------
select results_eq(
    $$ select version from supabase_migrations.schema_migrations where version <= '0008' order by version $$,
    $$ values ('0001'), ('0002'), ('0003'), ('0004'), ('0005'), ('0006'), ('0007'), ('0008') $$,
    'a clean reset applies migrations 0001-0008 in order');
-- Line endings normalized: hosted 0002 was pushed from a CRLF working copy (the same SQL).
select results_eq(
    $$ select version, md5(replace(array_to_string(statements, E'\n'), E'\r', ''))
         from supabase_migrations.schema_migrations where version < '0008' order by version $$,
    $$ values ('0001', '9477b175ef5b89ac07bdc8e01fca2800'), ('0002', 'ebcb2441c8c46e992d01ec2f48b990f9'),
              ('0003', 'a0cdc191e7c762e627eaca93bf1c0dc9'), ('0004', 'cfe38898bea85339e1fabf21fd07ecaf'),
              ('0005', 'a10c5f549c48fb042b72088bd1c7afd7'), ('0006', '94d268e3073dc3bdedb99a54fa8b690a'),
              ('0007', '8501de00cea54d9ff58be3e02f77f7ec') $$,
    'migrations 0001-0007 are unchanged (the statements applied on hosted)');
select has_table('public', 'verification_sessions', 'verification_sessions exists');
select has_table('public', 'verification_items', 'verification_items exists');
select has_table('public', 'verification_results', 'verification_results exists');
select enum_has_labels('public', 'verification_state',
    array['PLANNED', 'READY', 'IN_PROGRESS', 'SUBMITTED', 'EVALUATED', 'ABANDONED'], 'verification_state labels');
select enum_has_labels('public', 'verification_assessment_type',
    array['mcq', 'numeric', 'code', 'sql', 'short_response', 'reasoning'], 'assessment types (Appendix A.4)');
select enum_has_labels('public', 'verification_grader_type', array['MCQ_EXACT', 'NUMERIC_TOLERANCE', 'RUBRIC_AI'],
    'grader types (no sandbox grader in V1)');
select enum_has_labels('public', 'verification_evaluator_type', array['DETERMINISTIC', 'AI_RUBRIC'],
    'evaluator types');
select ok(
    (select bool_and(c.relrowsecurity) from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname in ('verification_sessions', 'verification_items', 'verification_results')),
    'RLS is enabled on the three verification tables');
select ok(
    not exists (select 1 from pg_constraint where conname = 'skill_ledger_no_verification_states_before_p6')
    and exists (select 1 from pg_trigger where tgname = 'skill_ledger_verification_guard'),
    'the P4-era VERIFIED / NEEDS_REVERIFICATION check is dropped and replaced by the verification guard');
select ok(
    (select (value -> 'planner' ->> 'max_daily_unsolicited')::int = 2
            and (value -> 'generation' ->> 'max_attempts')::int = 3
            and (value -> 'reverification' ->> 'min_contradicting_failures')::int = 2
            and value -> 'generation' -> 'supported_assessment_types'
                = '["mcq", "numeric", "short_response", "reasoning"]'::jsonb
       from public.policy_config where key = 'verification' and scope_type = 'global'),
    'verification policy is seeded (2 per day, initial + 2 regenerations, no code/sql, 2-failure contradiction)');
select ok(
    has_table_privilege('authenticated', 'public.verification_sessions', 'select')
    and has_table_privilege('authenticated', 'public.verification_results', 'select')
    and not has_table_privilege('authenticated', 'public.verification_sessions', 'insert, update, delete')
    and not has_table_privilege('authenticated', 'public.verification_results', 'insert, update, delete')
    and not has_table_privilege('authenticated', 'public.verification_items', 'select, insert, update, delete')
    and not has_table_privilege('anon', 'public.verification_sessions', 'select, insert, update, delete')
    and not has_table_privilege('anon', 'public.verification_items', 'select, insert, update, delete')
    and not has_table_privilege('anon', 'public.verification_results', 'select, insert, update, delete'),
    'grants: learners SELECT sessions and results only; the items (answer keys) are server-only; anon nothing');

-- Session planning guard ------------------------------------------------------------------
select throws_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000009', '80000000-0000-4000-8000-000000000001',
                              p_state => 'READY', p_ready => now()) $$,
    '23514', null, 'a session always starts PLANNED');
select throws_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000009', '80000000-0000-4000-8000-000000000001',
                              '84000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'a session cannot act on another learner''s recommendation');
select throws_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000009', '80000000-0000-4000-8000-000000000001',
                              p_course => '81000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'a session''s course is one of the learner''s own courses');
select lives_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000001', '80000000-0000-4000-8000-000000000001',
                              '84000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001') $$,
    'the planner creates a PLANNED session from the learner''s VERIFY recommendation');
select throws_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000009', '80000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'at most one active verification per learner and skill');

-- Lifecycle: legal transitions only --------------------------------------------------------
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'IN_PROGRESS') $$,
    '55000', null, 'illegal: PLANNED -> IN_PROGRESS');
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'READY') $$,
    '55000', null, 'a session is READY only with its validated challenge');

-- Challenge items
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000009', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001', p_difficulty => 0.9) $$,
    '23514', null, 'a challenge outside the planned difficulty band is refused');
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000009', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001',
                           p_prereqs => array['80000000-0000-4000-8000-000000000003']::uuid[]) $$,
    '23514', null, 'a challenge may only use known prerequisites of the skill');
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000009', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001', 'code') $$,
    '23514', null, 'code challenges cannot be issued in V1 (no sandbox grader)');
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000009', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001', p_choices => '[{"key": "A", "text": "x"}]') $$,
    '23514', null, 'an MCQ needs 2-6 choices');
select lives_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000001', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001',
                           p_prereqs => array['80000000-0000-4000-8000-000000000002']::uuid[]) $$,
    'a validated challenge in the band, with a known prerequisite, is stored');
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000008', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'one challenge per session (V1)');
select lives_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'READY') $$,
    'legal: PLANNED -> READY');
select throws_ok(
    $$ select pg_temp.item('86000000-0000-4000-8000-000000000008', '85000000-0000-4000-8000-000000000001',
                           '80000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'a challenge is only added while the session is PLANNED');
select throws_ok($$ select pg_temp.submit('85000000-0000-4000-8000-000000000001', 'k-early') $$,
    '55000', null, 'illegal: READY -> SUBMITTED (the learner must start first)');
select lives_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'IN_PROGRESS') $$,
    'legal: READY -> IN_PROGRESS');
select throws_ok(
    $$ update public.verification_sessions set planned_difficulty = 0.6
        where id = '85000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'the plan of a session is immutable');
select throws_ok(
    $$ select pg_temp.result('87000000-0000-4000-8000-000000000009', '86000000-0000-4000-8000-000000000001',
                             '85000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'nothing is graded before the learner submits');
select lives_ok($$ select pg_temp.submit('85000000-0000-4000-8000-000000000001', 'k-1') $$,
    'legal: IN_PROGRESS -> SUBMITTED stores the response before grading');
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'ABANDONED') $$,
    '55000', null, 'illegal: SUBMITTED -> ABANDONED');
select throws_ok(
    $$ update public.verification_sessions set submitted_response = '{"selected": ["A"]}'
        where id = '85000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'a submitted response is immutable');
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'EVALUATED') $$,
    '55000', null, 'a session is EVALUATED only with its graded result');
select throws_ok(
    $$ select pg_temp.result('87000000-0000-4000-8000-000000000009', '86000000-0000-4000-8000-000000000001',
                             '85000000-0000-4000-8000-000000000001', '{"selected": ["A"]}') $$,
    '23514', null, 'a result grades exactly the stored submission');
select lives_ok(
    $$ select pg_temp.result('87000000-0000-4000-8000-000000000001', '86000000-0000-4000-8000-000000000001',
                             '85000000-0000-4000-8000-000000000001') $$,
    'the grader stores the immutable graded result');
select throws_ok(
    $$ select pg_temp.result('87000000-0000-4000-8000-000000000002', '86000000-0000-4000-8000-000000000001',
                             '85000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'replay-safe: one graded result per item');
select throws_ok(
    $$ update public.verification_results set score = 0 where id = '87000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'verification results are append-only');
select throws_ok(
    $$ update public.verification_items set expected_answer = 'A' where id = '86000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'verification items are append-only');
select lives_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000001', 'EVALUATED') $$,
    'legal: SUBMITTED -> EVALUATED');
select throws_ok(
    $$ update public.verification_sessions set failure_code = 'LATE', failed_at = now()
        where id = '85000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'an EVALUATED session is final');

-- Abandonment: no result, no evidence ----------------------------------------------------------
select lives_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000002', '80000000-0000-4000-8000-000000000001');
       select pg_temp.item('86000000-0000-4000-8000-000000000002', '85000000-0000-4000-8000-000000000002',
                           '80000000-0000-4000-8000-000000000001');
       select pg_temp.advance('85000000-0000-4000-8000-000000000002', 'READY');
       select pg_temp.advance('85000000-0000-4000-8000-000000000002', 'IN_PROGRESS');
       select pg_temp.advance('85000000-0000-4000-8000-000000000002', 'ABANDONED') $$,
    'after an evaluation the skill can be verified again; legal: IN_PROGRESS -> ABANDONED');
select throws_ok(
    $$ select pg_temp.result('87000000-0000-4000-8000-000000000003', '86000000-0000-4000-8000-000000000002',
                             '85000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'an abandoned session is never graded (no incorrect-performance evidence)');
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000002', 'IN_PROGRESS') $$,
    '55000', null, 'an ABANDONED session is final');

-- Submission idempotency keys are unique per learner
select lives_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000003', '80000000-0000-4000-8000-000000000002');
       select pg_temp.item('86000000-0000-4000-8000-000000000003', '85000000-0000-4000-8000-000000000003',
                           '80000000-0000-4000-8000-000000000002', 'numeric');
       select pg_temp.advance('85000000-0000-4000-8000-000000000003', 'READY');
       select pg_temp.advance('85000000-0000-4000-8000-000000000003', 'IN_PROGRESS') $$,
    'a numeric challenge is started for another skill');
select throws_ok(
    $$ select pg_temp.submit('85000000-0000-4000-8000-000000000003', 'k-1', '{"answer": "42"}') $$,
    '23505', null, 'an Idempotency-Key is used for one submission of the learner');

-- VERIFICATION evidence names its result ------------------------------------------------------
select throws_ok($$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000099') $$,
    '23514', null, 'VERIFICATION evidence must name an existing verification result');
select throws_ok(
    $$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000001', p_signal => 'INCORRECT',
                                            p_outcome => 0) $$,
    '23514', null, 'the evidence outcome is the result''s grade');
select throws_ok(
    $$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000001',
                                            '00000000-0000-4000-8000-0000000008b1') $$,
    '23514', null, 'verification evidence belongs to the result''s learner');
select throws_ok(
    $$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000001', p_mapping => 0.9) $$,
    '23514', null, 'verification evidence has no mapping uncertainty (evidence confidence = grading confidence)');
select lives_ok($$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000001') $$,
    'the graded result becomes exactly one new VERIFICATION EvidenceEvent');
select results_eq(
    $$ select s.learner_id, s.skill_id, s.recommendation_id, i.id, e.actor::text, e.evidence_type::text
         from public.evidence_events e
         join public.verification_results r on r.id = e.source_id
         join public.verification_items i on i.id = r.item_id
         join public.verification_sessions s on s.id = i.session_id
        where e.source_type = 'VERIFICATION' and e.learner_id = '00000000-0000-4000-8000-0000000008a1' $$,
    $$ values ('00000000-0000-4000-8000-0000000008a1'::uuid, '80000000-0000-4000-8000-000000000001'::uuid,
               '84000000-0000-4000-8000-000000000001'::uuid, '86000000-0000-4000-8000-000000000001'::uuid,
               'STUDENT', 'VERIFICATION') $$,
    'provenance: evidence -> result -> item -> session -> learner / skill / recommendation');
select throws_ok($$ select pg_temp.verification_evidence('87000000-0000-4000-8000-000000000001') $$,
    '23505', null, 'replay-safe: one evidence event per verification result');
select hasnt_column('public', 'verification_results', 'evidence_event_id',
    'the immutable result is never updated with its evidence (the link is evidence.source_id)');
select is(
    (select count(*)::int from public.evidence_events e
      where e.source_type = 'VERIFICATION'
        and e.source_id in (select r.id from public.verification_results r
                             where r.session_id = '85000000-0000-4000-8000-000000000002')),
    0, 'the abandoned session produced no evidence');

-- Ledger: VERIFIED / NEEDS_REVERIFICATION need a passed verification ------------------------------
select throws_ok(
    $$ select pg_temp.ledger('VERIFIED', p_skill => '80000000-0000-4000-8000-000000000003') $$,
    '23514', null, 'VERIFIED without a passed SkillMirror verification is refused');
select lives_ok($$ select pg_temp.ledger('VERIFIED') $$,
    'verification evidence can now support VERIFIED');
select lives_ok($$ select pg_temp.ledger('NEEDS_REVERIFICATION') $$,
    'a previously verified skill can need re-verification');
select throws_ok(
    $$ select pg_temp.ledger('VERIFIED', '00000000-0000-4000-8000-0000000008b1') $$,
    '23514', null, 'another learner''s verification never verifies this learner');
select lives_ok(
    $$ select pg_temp.ledger('DEMONSTRATED', p_skill => '80000000-0000-4000-8000-000000000003') $$,
    'the other states need no verification');

-- Honest failure: a plan that could not produce a challenge -----------------------------------
select lives_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000004', '80000000-0000-4000-8000-000000000003');
       update public.verification_sessions
          set generation_attempts = 3, generation_rejections = '[{"attempt": 1, "reasons": ["DIFFICULTY_OUT_OF_BAND"]}]',
              failure_code = 'GENERATION_REJECTED', failed_at = now()
        where id = '85000000-0000-4000-8000-000000000004' $$,
    'a plan whose challenge failed validation records the failure');
select throws_ok($$ select pg_temp.advance('85000000-0000-4000-8000-000000000004', 'READY') $$,
    '55000', null, 'a failed plan never becomes READY');
select throws_ok(
    $$ update public.verification_sessions set generation_attempts = 1
        where id = '85000000-0000-4000-8000-000000000004' $$,
    '55000', null, 'a failed plan is closed');
select lives_ok(
    $$ select pg_temp.session('85000000-0000-4000-8000-000000000005', '80000000-0000-4000-8000-000000000003') $$,
    'a failed plan is not active: the skill can be planned again');
select throws_ok(
    $$ update public.verification_sessions set failure_code = 'X', failed_at = now()
        where id = '85000000-0000-4000-8000-000000000003' $$,
    '55000', null, 'a failure is only recorded where a pipeline step can fail (PLANNED / SUBMITTED)');

-- Row Level Security -----------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000008a1", "role": "authenticated"}';
select is((select count(*)::int from public.verification_sessions), 5, 'a learner reads their own sessions');
select is((select count(*)::int from public.verification_results), 1, 'a learner reads their own graded results');
select throws_ok($$ select count(*) from public.verification_items $$, '42501', null,
    'the challenge answer keys and rubrics are not readable by clients');
select throws_ok($$ select expected_answer from public.verification_items $$, '42501', null,
    'expected answers never reach the Data API');
select throws_ok(
    $$ insert into public.verification_sessions (learner_id, skill_id, trigger_type, reason_code, planned_difficulty,
                                                 difficulty_min, difficulty_max, plan_day, plan_timezone,
                                                 planner_version, planning_inputs)
       values ('00000000-0000-4000-8000-0000000008a1', '80000000-0000-4000-8000-000000000002', 'VERIFY', 'CLIENT',
               0.5, 0.4, 0.6, current_date, 'UTC', 'client/v1', '{}') $$,
    '42501', null, 'clients cannot create verification sessions');
select throws_ok(
    $$ update public.verification_sessions set state = 'EVALUATED' $$,
    '42501', null, 'clients cannot move a verification through its lifecycle');
select throws_ok(
    $$ insert into public.verification_results (item_id, session_id, learner_id, skill_id, response, score, pass,
                                                outcome_signal, outcome, evaluation, feedback, grading_confidence,
                                                evaluator_type, evaluator_version, policy_snapshot)
       values ('86000000-0000-4000-8000-000000000003', '85000000-0000-4000-8000-000000000003',
               '00000000-0000-4000-8000-0000000008a1', '80000000-0000-4000-8000-000000000002', '{"answer": "42"}',
               1, true, 'CORRECT', 1, '{}', 'Correct.', 1, 'DETERMINISTIC', 'client/v1', '{}') $$,
    '42501', null, 'clients cannot write graded results');
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000008b1", "role": "authenticated"}';
select is((select count(*)::int from public.verification_sessions), 0, 'another learner sees none of the sessions');
select is((select count(*)::int from public.verification_results), 0, 'another learner sees none of the results');
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.verification_sessions $$, '42501', null,
    'anon has no access to verification sessions');
select throws_ok($$ select count(*) from public.verification_results $$, '42501', null,
    'anon has no access to verification results');
reset role;
select ok(
    not has_function_privilege('authenticated', 'public.verification_sessions_guard_insert()', 'execute')
    and not has_function_privilege('authenticated', 'public.verification_sessions_guard_update()', 'execute')
    and not has_function_privilege('authenticated', 'public.verification_items_guard()', 'execute')
    and not has_function_privilege('authenticated', 'public.verification_results_guard()', 'execute')
    and not has_function_privilege('authenticated', 'public.evidence_events_verification_guard()', 'execute')
    and not has_function_privilege('authenticated', 'public.skill_ledger_verification_guard()', 'execute')
    and not has_function_privilege('anon', 'public.verification_sessions_guard_update()', 'execute'),
    'the P6 trigger functions are not executable by clients');

select * from finish();
rollback;
