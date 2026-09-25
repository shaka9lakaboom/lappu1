-- pgTAP tests for migration 0004 (ModelGateway result cache provenance, quota ledger).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(10);

insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version, input_hash,
                               output_hash, output, latency_ms, status, cache_key)
values ('36000000-0000-4000-8000-000000000001', 'job:cache-a', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash',
        'turn-analysis/v1', repeat('a', 64), repeat('b', 64), '{"segments": []}'::jsonb, 900, 'SUCCEEDED',
        repeat('c', 64));

select has_column('public', 'model_runs', 'cache_key', 'model_runs has cache_key');
select has_column('public', 'model_runs', 'cache_source_run_id', 'model_runs has cache_source_run_id');

select lives_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash,
                                      output_hash, output, latency_ms, status, cache_key, cache_source_run_id)
       values ('job:cache-b', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash', 'turn-analysis/v1', repeat('a', 64),
               repeat('b', 64), '{"segments": []}'::jsonb, 0, 'SUCCEEDED', repeat('c', 64),
               '36000000-0000-4000-8000-000000000001') $$,
    'a cache hit row references the run whose output it reused'
);
select throws_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash,
                                      latency_ms, status, cache_key)
       values ('job:cache-c', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash', 'turn-analysis/v1', repeat('a', 64),
               10, 'INVALID_OUTPUT', repeat('c', 64)) $$,
    '23514', null,
    'an invalid output can never carry a cache key'
);
select throws_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash,
                                      latency_ms, status, cache_key)
       values ('job:cache-d', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash', 'turn-analysis/v1', repeat('a', 64),
               10, 'RATE_LIMITED', repeat('c', 64)) $$,
    '23514', null,
    'a rate-limited (429) call can never carry a cache key'
);
select throws_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash,
                                      latency_ms, status, cache_source_run_id)
       values ('job:cache-e', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash', 'turn-analysis/v1', repeat('a', 64),
               0, 'FAILED', '36000000-0000-4000-8000-000000000001') $$,
    '23514', null,
    'a cache hit must be a success with a cache key'
);
select throws_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash,
                                      latency_ms, status, cache_key, cache_source_run_id, attempt)
       values ('job:cache-f', 'TURN_ANALYSIS', 'google', 'gemini-3.7-flash', 'turn-analysis/v1', repeat('a', 64),
               0, 'SUCCEEDED', repeat('c', 64), '36000000-0000-4000-8000-000000000001', 2) $$,
    '23514', null,
    'a cache hit is never a repair attempt'
);
select throws_ok(
    $$ update public.model_runs set cache_key = repeat('f', 64)
        where id = '36000000-0000-4000-8000-000000000001' $$,
    '55000', null,
    'cache provenance stays append-only'
);
select is(
    (select count(*)::int from public.model_runs
      where provider = 'google' and model = 'gemini-3.7-flash' and cache_source_run_id is null
        and trace_id like 'job:cache-%'),
    1,
    'only the provider request counts toward the daily budget, not the cache hit'
);
select ok(
    (select count(*) = 2 from pg_indexes where schemaname = 'public' and tablename = 'model_runs'
        and indexname in ('model_runs_cache_lookup_idx', 'model_runs_provider_requests_idx')),
    'cache lookup and provider request indexes exist'
);

select * from finish();
rollback;
