-- pgTAP tests for migration 0006 (P4 skill_ledger: mastery + AI Assistance Debt).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(15);

insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000006a1', 'p4-a@test.invalid'),
    ('00000000-0000-4000-8000-0000000006b1', 'p4-b@test.invalid');
insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status, source)
values ('60000000-0000-4000-8000-000000000001', 'p4t-recursion', 'P4T Recursion Basics', 'p4t recursion basic',
        'Write recursive functions with a correct base case.', 'SKILL', 'ACTIVE', 'SEED');

-- A ledger row as the backend writes it, with overridable state/debt fields.
create function pg_temp.ledger(
    p_state text default 'UNKNOWN', p_alpha float8 default 1.7875, p_beta float8 default 1,
    p_mean float8 default null, p_support float8 default 0.7875, p_eligible boolean default false,
    p_score float8 default 0)
returns void language sql as $$
    insert into public.skill_ledger (
        learner_id, skill_id, alpha, beta, mastery_mean, support, mastery_state, debt_score, debt_eligible,
        evidence_count, performance_evidence_count, recent_delegation_count, computed_as_of,
        algorithm_version, policy_snapshot)
    values ('00000000-0000-4000-8000-0000000006a1', '60000000-0000-4000-8000-000000000001', p_alpha, p_beta,
            coalesce(p_mean, p_alpha / (p_alpha + p_beta)), p_support, p_state::public.mastery_state, p_score,
            p_eligible, 1, 1, 0, now(), 'ledger/p4-v1', '{}'::jsonb);
$$;

select has_table('public', 'skill_ledger', 'skill_ledger exists');
select enum_has_labels('public', 'mastery_state',
    array['UNKNOWN', 'EMERGING', 'DEVELOPING', 'DEMONSTRATED', 'VERIFIED', 'NEEDS_REVERIFICATION'],
    'mastery_state labels');
select col_is_pk('public', 'skill_ledger', array['learner_id', 'skill_id'], 'one ledger row per learner and skill');
select ok(
    (select c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname = 'skill_ledger'),
    'RLS is enabled on skill_ledger'
);
select ok(
    (select (value ->> 'min_recent_delegations')::int = 2
            and (value -> 'verification_factor' ->> 'unverified')::float8 = 0.6
       from public.policy_config where key = 'debt' and scope_type = 'global')
    and exists (select 1 from public.policy_config where key = 'mastery'
                 and (value ->> 'unknown_min_support')::float8 = 1.0
                 and (value ->> 'recency_half_life_days')::float8 = 180),
    'mastery + debt policy are seeded (>= 2 delegations, 180-day half-life, support gate 1.0)'
);

select throws_ok($$ select pg_temp.ledger(p_state => 'VERIFIED') $$, '23514', null,
    'VERIFIED is unreachable before P6 verification');
select throws_ok($$ select pg_temp.ledger(p_state => 'NEEDS_REVERIFICATION') $$, '23514', null,
    'NEEDS_REVERIFICATION is unreachable before P6 verification');
select throws_ok($$ select pg_temp.ledger(p_score => 12) $$, '23514', null,
    'no debt score without debt eligibility');
select throws_ok($$ select pg_temp.ledger(p_mean => 0.9) $$, '23514', null,
    'mastery_mean is alpha / (alpha + beta)');
select lives_ok($$ select pg_temp.ledger() $$, 'a derived ledger row is written');
select throws_ok($$ select pg_temp.ledger() $$, '23505', null, 'at most one row per learner and skill');

set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000006a1", "role": "authenticated"}';
select is((select count(*)::int from public.skill_ledger), 1, 'a learner reads their own ledger');
select throws_ok(
    $$ update public.skill_ledger set debt_score = 0, mastery_state = 'DEMONSTRATED' $$,
    '42501', null, 'clients cannot modify the ledger');
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000006b1", "role": "authenticated"}';
select is((select count(*)::int from public.skill_ledger), 0, 'another learner sees none of it');
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.skill_ledger $$, '42501', null, 'anon has no access to the ledger');
reset role;

select * from finish();
rollback;
