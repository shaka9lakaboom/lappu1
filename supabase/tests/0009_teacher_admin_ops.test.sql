-- pgTAP tests for migration 0009 (P7 teacher + admin operations: audit events, benchmark runs,
-- manual job retries, skill-candidate review, teacher memberships) and the RLS sweep over every
-- public table. Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(75);

-- Fixtures, written as the table owner (the way the backend writes) -------
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000009a1', 'p7-admin@test.invalid'),
    ('00000000-0000-4000-8000-0000000009b1', 'p7-teacher@test.invalid'),
    ('00000000-0000-4000-8000-0000000009c1', 'p7-s1@test.invalid'),
    ('00000000-0000-4000-8000-0000000009c2', 'p7-s2@test.invalid'),
    ('00000000-0000-4000-8000-0000000009c3', 'p7-s3@test.invalid'),
    ('00000000-0000-4000-8000-0000000009d1', 'p7-other@test.invalid');
-- Roles are an operator action on the profile (never a JWT claim).
update public.profiles set role = 'ADMIN' where id = '00000000-0000-4000-8000-0000000009a1';
update public.profiles set role = 'TEACHER' where id = '00000000-0000-4000-8000-0000000009b1';

insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status,
                                difficulty_band, source)
values
    ('90000000-0000-4000-8000-000000000001', 'p7t-sql-joins', 'P7T SQL Joins', 'p7t sql joins',
     'Combine rows of two tables with a join.', 'SKILL', 'ACTIVE', 2, 'SEED'),
    ('90000000-0000-4000-8000-000000000002', 'p7t-group-by', 'P7T Group By', 'p7t group by',
     'Aggregate rows per group with GROUP BY.', 'SKILL', 'ACTIVE', 2, 'SEED');

insert into public.courses (id, owner_id, name, graph_status, graph_version)
values ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009c1', 'P7T SQL', 'READY', 1);
insert into public.course_memberships (course_id, user_id, role)
values
    ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009c1', 'STUDENT'),
    ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009c2', 'STUDENT'),
    ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009c3', 'STUDENT');
insert into public.course_skills (course_id, skill_id, importance, source, graph_version)
values
    ('91000000-0000-4000-8000-000000000001', '90000000-0000-4000-8000-000000000001', 0.8, 'SEED', 1),
    ('91000000-0000-4000-8000-000000000001', '90000000-0000-4000-8000-000000000002', 0.6, 'SEED', 1);

-- Each student has a ledger row and one piece of (assessment) evidence.
insert into public.skill_ledger (learner_id, skill_id, alpha, beta, mastery_mean, support, mastery_state,
                                 evidence_count, performance_evidence_count, recent_delegation_count,
                                 computed_as_of, algorithm_version, policy_snapshot)
select s, '90000000-0000-4000-8000-000000000001', 2.0, 1.0, 2.0 / 3.0, 1.0, 'DEVELOPING', 1, 1, 0, now(),
       'ledger/p6-v1', '{}'::jsonb
  from unnest(array['00000000-0000-4000-8000-0000000009c1', '00000000-0000-4000-8000-0000000009c2',
                    '00000000-0000-4000-8000-0000000009c3']::uuid[]) s;
insert into public.evidence_events (
    learner_id, skill_id, source_type, source_id, evidence_type, actor, outcome_signal, outcome, difficulty,
    difficulty_multiplier, independence, base_weight, strength, mapping_confidence, attribution_confidence,
    grading_confidence, evidence_confidence, evidence_span, qualification_reason, qualifier_version,
    policy_snapshot, occurred_at)
select s, '90000000-0000-4000-8000-000000000001', 'ASSESSMENT', gen_random_uuid(), 'TEACHER_EVIDENCE', 'STUDENT',
       'CORRECT', 1, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, null, 1.0, '{}'::jsonb, 'GRADED', 'assessment/test-v1',
       '{}'::jsonb, now()
  from unnest(array['00000000-0000-4000-8000-0000000009c1', '00000000-0000-4000-8000-0000000009c2',
                    '00000000-0000-4000-8000-0000000009c3']::uuid[]) s;

-- Jobs as the worker leaves them.
insert into public.processing_jobs (id, job_type, entity_type, entity_id, learner_id, state, attempts, last_error)
values
    ('92000000-0000-4000-8000-000000000001', 'BOOTSTRAP_COURSE_GRAPH', 'course', gen_random_uuid(),
     '00000000-0000-4000-8000-0000000009c1', 'FAILED', 3, 'ModelCallError: boom'),
    ('92000000-0000-4000-8000-000000000002', 'PROCESS_RAW_MESSAGE', 'raw_message', gen_random_uuid(),
     '00000000-0000-4000-8000-0000000009c1', 'COMPLETED', 1, null),
    ('92000000-0000-4000-8000-000000000003', 'BOOTSTRAP_COURSE_GRAPH', 'course', gen_random_uuid(),
     '00000000-0000-4000-8000-0000000009c1', 'COMPLETED', 1, null),
    ('92000000-0000-4000-8000-000000000004', 'PROCESS_RAW_MESSAGE', 'raw_message', gen_random_uuid(),
     '00000000-0000-4000-8000-0000000009c1', 'FAILED', 3, 'RuntimeError: x');

-- The manual retry an admin makes (P7 retry service).
create function pg_temp.retry(p_job uuid)
returns void language sql as $$
    update public.processing_jobs
       set state = 'PENDING', attempts = 0, available_at = now(), last_error = null, completed_at = null,
           outcome = 'MANUAL_RETRY', manual_retry_count = manual_retry_count + 1, last_manual_retry_at = now()
     where id = p_job;
$$;

create function pg_temp.audit(
    p_key text, p_actor uuid default '00000000-0000-4000-8000-0000000009a1', p_type text default 'USER',
    p_role text default 'ADMIN', p_hash text default repeat('a', 64))
returns void language sql as $$
    insert into public.audit_events (actor_type, actor_id, actor_role, action, entity_type, entity_id, metadata,
                                     client_request_id, request_hash)
    values (p_type::public.audit_actor_type, p_actor, p_role::public.app_role, 'JOB_RETRY', 'processing_job',
            '92000000-0000-4000-8000-000000000001', '{"previous_state": "FAILED"}', p_key,
            case when p_key is null then null else p_hash end);
$$;

create function pg_temp.benchmark(
    p_mode text, p_passed int default 120, p_failed int default 0, p_requests int default 0,
    p_model text default null)
returns void language sql as $$
    insert into public.benchmark_runs (set_name, set_version, mode, provider, model, policy_hash, case_count,
                                       passed_count, failed_count, hard_gates, verdict, provider_requests,
                                       started_at, finished_at)
    values ('critical-gate', 'v1', p_mode::public.benchmark_mode,
            case when p_model is null then null else 'google' end, p_model, repeat('b', 64), 120,
            p_passed, p_failed, '{"false_debt": {"value": 0, "threshold": 0, "pass": true}}',
            case when p_failed = 0 then 'PASS' else 'FAIL' end::public.benchmark_verdict, p_requests,
            now() - interval '1 minute', now());
$$;

-- Schema: a clean reset applies 0001-0009; 0001-0008 are the migrations on hosted ----------
select results_eq(
    $$ select version from supabase_migrations.schema_migrations order by version $$,
    $$ values ('0001'), ('0002'), ('0003'), ('0004'), ('0005'), ('0006'), ('0007'), ('0008'), ('0009') $$,
    'a clean reset applies migrations 0001-0009 in order');
-- Line endings normalized: hosted 0002 and 0008 were pushed from CRLF working copies (the same SQL;
-- the CLI records the file's bytes). Hosted and a fresh LF checkout agree on these values.
select results_eq(
    $$ select version, md5(replace(array_to_string(statements, E'\n'), E'\r', ''))
         from supabase_migrations.schema_migrations where version < '0009' order by version $$,
    $$ values ('0001', '9477b175ef5b89ac07bdc8e01fca2800'), ('0002', 'ebcb2441c8c46e992d01ec2f48b990f9'),
              ('0003', 'a0cdc191e7c762e627eaca93bf1c0dc9'), ('0004', 'cfe38898bea85339e1fabf21fd07ecaf'),
              ('0005', 'a10c5f549c48fb042b72088bd1c7afd7'), ('0006', '94d268e3073dc3bdedb99a54fa8b690a'),
              ('0007', '8501de00cea54d9ff58be3e02f77f7ec'), ('0008', '1f415b484e19e8b9ae657de7a4b3b656') $$,
    'migrations 0001-0008 are unchanged (the statements applied on hosted)');
select has_table('public', 'audit_events', 'audit_events exists');
select has_table('public', 'benchmark_runs', 'benchmark_runs exists');
select enum_has_labels('public', 'audit_actor_type', array['USER', 'OPERATOR'], 'audit_actor_type labels');
select enum_has_labels('public', 'audit_action',
    array['JOB_RETRY', 'JOB_RESUME_ATTRIBUTION', 'CANDIDATE_APPROVE', 'CANDIDATE_MERGE', 'CANDIDATE_REJECT',
          'COURSE_MEMBER_ADD', 'ROLE_CHANGE'], 'audit_action labels');
select enum_has_labels('public', 'benchmark_mode', array['DETERMINISTIC', 'REPLAY', 'LIVE'], 'benchmark_mode labels');
select enum_has_labels('public', 'benchmark_verdict', array['PASS', 'FAIL'], 'benchmark_verdict labels');
select columns_are('public', 'processing_jobs',
    array['id', 'job_type', 'entity_type', 'entity_id', 'learner_id', 'state', 'attempts', 'max_attempts',
          'available_at', 'locked_at', 'locked_by', 'last_error', 'completed_at', 'created_at', 'updated_at',
          'outcome', 'manual_retry_count', 'last_manual_retry_at'],
    'processing_jobs gains only the manual retry count and time (no admin id on a learner-readable row)');
select has_column('public', 'skill_candidates', 'reviewed_by', 'skill_candidates.reviewed_by exists');
select has_column('public', 'skill_candidates', 'review_note', 'skill_candidates.review_note exists');
select is((select value from public.policy_config where key = 'teacher_view' and scope_type = 'global'),
    '{"min_cohort": 3, "window_days": 30, "top_n": 10}'::jsonb, 'teacher_view policy seeded');
select is((select count(*)::int from public.policy_config where scope_type = 'global'), 12,
    'twelve global policy keys');

-- RLS sweep over every public table --------------------------------------------------------------
select is(
    (select array_agg(c.relname::text order by c.relname) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity),
    null::text[], 'RLS is enabled on every public table');
select is(
    (select array_agg(c.relname::text order by c.relname) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'v', 'm')
       and (has_table_privilege('anon', c.oid, 'SELECT') or has_table_privilege('anon', c.oid, 'INSERT')
            or has_table_privilege('anon', c.oid, 'UPDATE') or has_table_privilege('anon', c.oid, 'DELETE'))),
    null::text[], 'anon has no privilege on any public table or view');
select is(
    (select array_agg(c.relname::text order by c.relname) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'v', 'm')
       and (has_table_privilege('authenticated', c.oid, 'INSERT')
            or has_table_privilege('authenticated', c.oid, 'UPDATE')
            or has_table_privilege('authenticated', c.oid, 'DELETE')
            or has_table_privilege('authenticated', c.oid, 'TRUNCATE'))),
    null::text[], 'signed-in clients cannot write any public table (profiles: two columns only)');
select is(
    (select array_agg(c.relname::text order by c.relname) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r' and has_table_privilege('authenticated', c.oid, 'SELECT')
       and not exists (select 1 from pg_policy p where p.polrelid = c.oid)),
    null::text[], 'every client-readable table has a row policy');
select is(
    (select array_agg(c.relname::text order by c.relname) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relname in ('audit_events', 'benchmark_runs', 'model_runs', 'policy_config',
                                                  'skill_candidates', 'skill_embeddings', 'verification_items')
       and (has_table_privilege('authenticated', c.oid, 'SELECT')
            or exists (select 1 from pg_policy p where p.polrelid = c.oid))),
    null::text[], 'the server-only tables have no client privilege and no policy');
select is(
    (select array_agg(p.proname::text order by p.proname) from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public' and p.prosecdef
       and (has_function_privilege('anon', p.oid, 'execute')
            or has_function_privilege('authenticated', p.oid, 'execute'))),
    null::text[], 'no SECURITY DEFINER function in public is executable by clients');
select ok(
    not has_function_privilege('authenticated', 'public.audit_events_guard_update()', 'execute')
    and not has_function_privilege('authenticated', 'public.processing_jobs_guard_manual_retry()', 'execute')
    and not has_function_privilege('authenticated', 'public.skill_candidates_guard_update()', 'execute')
    and not has_function_privilege('authenticated', 'public.course_memberships_guard_teacher()', 'execute')
    and not has_function_privilege('authenticated', 'public.profiles_guard_teacher_role()', 'execute')
    and not has_function_privilege('anon', 'public.skill_candidates_guard_update()', 'execute'),
    'the P7 trigger functions are not executable by clients');

-- audit_events --------------------------------------------------------------------------------
select lives_ok($$ select pg_temp.audit('retry-1') $$, 'an admin mutation is audited with its Idempotency-Key');
select throws_ok($$ select pg_temp.audit('retry-1') $$, '23505', null,
    'one audit row per admin Idempotency-Key');
select lives_ok($$ select pg_temp.audit('retry-1', p_actor => '00000000-0000-4000-8000-0000000009b1', p_role => 'TEACHER') $$,
    'the key is scoped to its actor');
select throws_ok($$ select pg_temp.audit(null) $$, '23514', null,
    'an API mutation always carries its Idempotency-Key');
select throws_ok($$ select pg_temp.audit('k2', p_role => null) $$, '23514', null,
    'a user actor records the profile role it acted with');
select lives_ok($$ select pg_temp.audit(null, p_actor => null, p_type => 'OPERATOR', p_role => null) $$,
    'an operator script is audited without an account');
select throws_ok($$ select pg_temp.audit(null, p_type => 'OPERATOR', p_role => null) $$, '23514', null,
    'an operator action names no account');
select throws_ok($$ update public.audit_events set metadata = '{}' $$, '55000', null, 'audit_events is append-only');

-- benchmark_runs ------------------------------------------------------------------------------
select lives_ok($$ select pg_temp.benchmark('DETERMINISTIC') $$, 'a deterministic run is recorded');
select lives_ok($$ select pg_temp.benchmark('LIVE', 110, 10, 150, 'gemini-3.5-flash-lite') $$,
    'a live run records its model and the requests it spent');
select throws_ok($$ select pg_temp.benchmark('DETERMINISTIC', 100, 10) $$, '23514', null,
    'passed + failed + blocked = cases');
select throws_ok($$ select pg_temp.benchmark('REPLAY', 120, 0, 5, 'gemini-3.5-flash-lite') $$, '23514', null,
    'a replay run makes no provider request');
select throws_ok($$ select pg_temp.benchmark('REPLAY') $$, '23514', null,
    'a replay run names the model whose recordings it replays');
select throws_ok($$ update public.benchmark_runs set verdict = 'PASS' $$, '55000', null,
    'benchmark_runs is append-only');

-- processing_jobs: manual retry ---------------------------------------------------------------
select lives_ok($$ select pg_temp.retry('92000000-0000-4000-8000-000000000001') $$,
    'a FAILED job is retried manually');
select is((select (state::text, attempts, manual_retry_count, last_manual_retry_at is not null)
             from public.processing_jobs where id = '92000000-0000-4000-8000-000000000001'),
    ('PENDING'::text, 0, 1, true), 'the retry re-queues it PENDING with a fresh attempt budget and counts one');
select throws_ok($$ select pg_temp.retry('92000000-0000-4000-8000-000000000001') $$, '55000', null,
    'a PENDING job is not retried manually');
select lives_ok(
    $$ update public.processing_jobs set state = 'PROCESSING', locked_at = now(), locked_by = 'w', attempts = 1
        where id = '92000000-0000-4000-8000-000000000001' $$,
    'the worker claims the retried job as usual');
select throws_ok(
    $$ update public.processing_jobs set manual_retry_count = 3, state = 'PENDING', locked_at = null, attempts = 0,
                                         last_manual_retry_at = now()
        where id = '92000000-0000-4000-8000-000000000004' $$,
    '55000', null, 'a manual retry counts exactly one');
select throws_ok(
    $$ update public.processing_jobs set last_manual_retry_at = now()
        where id = '92000000-0000-4000-8000-000000000004' $$,
    '55000', null, 'manual retry metadata changes only with a retry');
select throws_ok(
    $$ update public.processing_jobs set manual_retry_count = 1, last_manual_retry_at = now()
        where id = '92000000-0000-4000-8000-000000000004' $$,
    '55000', null, 'a manual retry always re-queues the job');
select lives_ok($$ select pg_temp.retry('92000000-0000-4000-8000-000000000002') $$,
    'a COMPLETED raw-message job can resume its pending attribution');
select throws_ok($$ select pg_temp.retry('92000000-0000-4000-8000-000000000003') $$, '55000', null,
    'a COMPLETED course bootstrap is never re-run manually');
select throws_ok(
    $$ do $body$ begin
         for i in 1..6 loop
           update public.processing_jobs set state = 'FAILED' where id = '92000000-0000-4000-8000-000000000004';
           perform pg_temp.retry('92000000-0000-4000-8000-000000000004');
         end loop;
       end $body$ $$,
    '23514', null, 'at most five manual retries per job');

-- skill_candidates: review --------------------------------------------------------------------
insert into public.skill_candidates (id, canonical_name, normalized_name)
values
    ('93000000-0000-4000-8000-000000000001', 'P7T Window Functions', 'p7t window function'),
    ('93000000-0000-4000-8000-000000000002', 'P7T Merge Name', 'p7t merge name'),
    ('93000000-0000-4000-8000-000000000003', 'P7T Approved Skill', 'p7t approved skill'),
    ('93000000-0000-4000-8000-000000000004', 'P7T SQL Joins', 'p7t sql joins');

create function pg_temp.review(p_id uuid, p_status text, p_resolved uuid default null,
                               p_by uuid default '00000000-0000-4000-8000-0000000009a1')
returns void language sql as $$
    update public.skill_candidates
       set status = p_status::public.skill_candidate_status, resolved_skill_id = p_resolved, reviewed_by = p_by,
           reviewed_at = now(), review_note = 'reviewed'
     where id = p_id;
$$;

select throws_ok(
    $$ update public.skill_candidates set reviewed_at = now() where id = '93000000-0000-4000-8000-000000000001' $$,
    '23514', null, 'a pending candidate has no review');
select throws_ok(
    $$ select pg_temp.review('93000000-0000-4000-8000-000000000001', 'REJECTED',
                             p_by => '00000000-0000-4000-8000-0000000009b1') $$,
    '23514', null, 'only an ADMIN reviews a candidate');
select lives_ok($$ select pg_temp.review('93000000-0000-4000-8000-000000000001', 'REJECTED') $$,
    'an admin rejects a candidate');
select throws_ok(
    $$ update public.skill_candidates set status = 'PENDING_REVIEW', reviewed_at = null, reviewed_by = null,
                                          review_note = null
        where id = '93000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'a reviewed candidate is final');
select lives_ok(
    $$ update public.skill_candidates set occurrences = occurrences + 1
        where id = '93000000-0000-4000-8000-000000000001' $$,
    'a rejected name keeps counting its later proposals');
select throws_ok(
    $$ update public.skill_candidates set occurrences = 1 where id = '93000000-0000-4000-8000-000000000001' $$,
    '55000', null, 'occurrences never decrease');
select throws_ok(
    $$ insert into public.skill_candidates (id, canonical_name, normalized_name)
       values ('93000000-0000-4000-8000-000000000009', 'P7T Window Functions', 'p7t window function');
       select pg_temp.review('93000000-0000-4000-8000-000000000009', 'REJECTED') $$,
    '23505', null, 'a rejected name has exactly one row');
select throws_ok(
    $$ select pg_temp.review('93000000-0000-4000-8000-000000000002', 'MERGED', '90000000-0000-4000-8000-000000000002') $$,
    '23514', null, 'a merge needs the name to resolve to the target skill');
select lives_ok(
    $$ insert into public.skill_aliases (skill_id, alias, normalized_alias, alias_kind, source)
       values ('90000000-0000-4000-8000-000000000002', 'P7T Merge Name', 'p7t merge name', 'VARIANT',
               'CANDIDATE_APPROVAL');
       select pg_temp.review('93000000-0000-4000-8000-000000000002', 'MERGED', '90000000-0000-4000-8000-000000000002') $$,
    'MERGE: the name becomes an alias of an existing skill');
select throws_ok(
    $$ select pg_temp.review('93000000-0000-4000-8000-000000000004', 'APPROVED', '90000000-0000-4000-8000-000000000001') $$,
    '23514', null, 'APPROVE names a node created by the approval, not an existing one');
select lives_ok(
    $$ insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status,
                                       source)
       values ('90000000-0000-4000-8000-000000000003', 'p7t-approved-skill', 'P7T Approved Skill',
               'p7t approved skill', 'A skill approved from a candidate.', 'SKILL', 'ACTIVE', 'CANDIDATE_APPROVAL');
       select pg_temp.review('93000000-0000-4000-8000-000000000003', 'APPROVED', '90000000-0000-4000-8000-000000000003') $$,
    'APPROVE: a new ACTIVE node through the second controlled path (§8.2)');
select is(
    (select array_agg(status::text || ':' || coalesce(resolved_skill_id::text, '-') order by id)
       from public.skill_candidates where id::text like '93000000-%'),
    array['REJECTED:-', 'MERGED:90000000-0000-4000-8000-000000000002', 'APPROVED:90000000-0000-4000-8000-000000000003',
          'PENDING_REVIEW:-'],
    'the reviews resolved exactly as recorded');

-- course_memberships: teacher membership validation ---------------------------------------------
select throws_ok(
    $$ insert into public.course_memberships (course_id, user_id, role)
       values ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009d1', 'TEACHER') $$,
    '23514', null, 'a STUDENT profile cannot hold a TEACHER membership');
select throws_ok(
    $$ update public.course_memberships set role = 'TEACHER'
        where user_id = '00000000-0000-4000-8000-0000000009c2' $$,
    '23514', null, 'nor become a teacher of their course by an update');
select lives_ok(
    $$ insert into public.course_memberships (course_id, user_id, role)
       values ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009b1', 'TEACHER') $$,
    'a TEACHER profile teaches the course');
select lives_ok(
    $$ insert into public.courses (id, owner_id, name) values ('91000000-0000-4000-8000-000000000002',
                                  '00000000-0000-4000-8000-0000000009c1', 'P7T other');
       insert into public.course_memberships (course_id, user_id, role)
       values ('91000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000009a1', 'TEACHER') $$,
    'an ADMIN profile can teach too');
select throws_ok(
    $$ update public.profiles set role = 'STUDENT' where id = '00000000-0000-4000-8000-0000000009b1' $$,
    '23514', null, 'a teacher is not demoted while teaching');
select lives_ok(
    $$ update public.profiles set role = 'ADMIN' where id = '00000000-0000-4000-8000-0000000009b1';
       update public.profiles set role = 'TEACHER' where id = '00000000-0000-4000-8000-0000000009b1' $$,
    'TEACHER <-> ADMIN keeps the memberships valid');

-- Row Level Security as a teacher: the course, not the students' rows ---------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000009b1", "role": "authenticated", "user_metadata": {"role": "ADMIN"}}';
select is((select count(*)::int from public.courses where id = '91000000-0000-4000-8000-000000000001'), 1,
    'a teacher reads the course they teach');
select is((select count(*)::int from public.course_skills where course_id = '91000000-0000-4000-8000-000000000001'),
    2, 'and its skills');
select is((select count(*)::int from public.course_memberships), 1,
    'but only their own membership row (no roster through the Data API)');
select is(
    (select count(*)::int from public.skill_ledger) + (select count(*)::int from public.evidence_events)
    + (select count(*)::int from public.processing_jobs) + (select count(*)::int from public.raw_messages)
    + (select count(*)::int from public.recommendations) + (select count(*)::int from public.feedback)
    + (select count(*)::int from public.activity_segments) + (select count(*)::int from public.skill_mappings)
    + (select count(*)::int from public.attributions) + (select count(*)::int from public.verification_sessions),
    0, 'a teacher reads none of the students'' ledger, evidence, jobs, activity or feedback rows');
select is((select role::text from public.profiles), 'TEACHER',
    'the profile role is the database''s, whatever the token metadata claims');
select throws_ok($$ select count(*) from public.audit_events $$, '42501', null, 'audit events are server-only');
select throws_ok($$ select count(*) from public.benchmark_runs $$, '42501', null, 'benchmark runs are server-only');
select throws_ok($$ select count(*) from public.skill_candidates $$, '42501', null,
    'skill candidates stay server-only');
select throws_ok(
    $$ insert into public.course_memberships (course_id, user_id, role)
       values ('91000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000009d1', 'STUDENT') $$,
    '42501', null, 'clients cannot enroll anyone');
select throws_ok(
    $$ update public.profiles set role = 'ADMIN' where id = '00000000-0000-4000-8000-0000000009b1' $$,
    '42501', null, 'clients cannot change their role');
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000009c1", "role": "authenticated"}';
select is((select count(*)::int from public.skill_ledger), 1, 'a student still reads their own ledger row');
select is((select count(*)::int from public.course_memberships), 1, 'and only their own membership');
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.audit_events $$, '42501', null, 'anon has no access to audit events');
reset role;

select * from finish();
rollback;
