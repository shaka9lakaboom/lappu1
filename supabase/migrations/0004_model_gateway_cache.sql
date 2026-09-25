-- SkillMirror migration 0004: ModelGateway exact result cache provenance and quota ledger
-- (ADR 0004). Additive only: two nullable columns, two checks, two partial indexes.
--
-- * cache_key identifies an exact request: sha256 of provider, model, task_type,
--   prompt_version and the input hash of the ORIGINAL request. It is set only on a run
--   whose output was validated (the first attempt, or the repair of an invalid first
--   attempt) and on cache hits. Failed or invalid runs never carry one, so a failure
--   can never be served as a cached success.
-- * cache_source_run_id marks a cache hit: the gateway made NO provider request and
--   reused the validated output of that run. Every other row is one provider request,
--   which is what the daily request budget counts.

alter table public.model_runs
    add column cache_key text check (cache_key is null or cache_key ~ '^[0-9a-f]{64}$'),
    add column cache_source_run_id uuid references public.model_runs (id);

comment on column public.model_runs.cache_key is
    'Exact-request key (provider, model, task_type, prompt_version, original input hash). Only on validated outputs and cache hits.';
comment on column public.model_runs.cache_source_run_id is
    'Cache hit: no provider request was made; the validated output of this run was reused.';

alter table public.model_runs
    add constraint model_runs_cache_key_only_on_success
        check (cache_key is null or status = 'SUCCEEDED'),
    add constraint model_runs_cache_hit_shape
        check (cache_source_run_id is null
               or (status = 'SUCCEEDED' and cache_key is not null
                   and attempt = 1 and repair_of_id is null and cache_source_run_id <> id));

-- Cache lookup: the newest provider-produced success for a key.
create index model_runs_cache_lookup_idx on public.model_runs (cache_key, created_at desc)
    where cache_source_run_id is null and status = 'SUCCEEDED';

-- Daily request budget: provider requests per (provider, model) since the quota day began.
create index model_runs_provider_requests_idx on public.model_runs (provider, model, created_at)
    where cache_source_run_id is null;
