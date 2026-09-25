-- 0009_teacher_admin_ops.sql
-- SkillMirror P7: teacher + admin operations (architecture §4, §7.1, §8.2, §12.3, §13, §17;
-- ADR 0008), and the benchmark run store that P8 writes.
--
-- * audit_events: one append-only row per admin/operator mutation (job retry, candidate review,
--   course enrollment, role change), carrying the Idempotency-Key of the request that made it.
--   Ids, states and codes only: never captured text, prompts, model output or secrets.
-- * benchmark_runs: one append-only row per benchmark run (deterministic / replay / live), its
--   hard gates, calibration metrics and verdict. Written by the P8 runner, read by the admin UI.
-- * processing_jobs: manual retry bookkeeping. A manual retry re-queues a FAILED job (or resumes
--   the pending attribution of a COMPLETED raw-message job) with a fresh attempt budget, at most
--   5 times per job. Who retried lives in audit_events (processing_jobs is learner-readable).
-- * skill_candidates: review metadata. PENDING_REVIEW -> APPROVED / MERGED / REJECTED by an
--   ADMIN; a reviewed candidate is final (only its occurrence count keeps growing), an approved
--   or merged name must resolve to its skill, and a rejected name stays rejected (one row).
-- * course_memberships: a TEACHER membership needs a TEACHER or ADMIN profile, and a profile
--   holding TEACHER memberships cannot be demoted to STUDENT.
-- * policy_config: `teacher_view` (the minimum cohort and the windows of the teacher overview).
--   It is not one of the worker's intelligence keys, so a worker never depends on this migration.
--
-- Teacher aggregates are computed by the backend over its service connection. No teacher RLS
-- policy is added: a teacher reads, through the Data API, exactly what any member reads (the
-- course, its skills, their own membership row) and never another learner's rows. The two new
-- tables are server-only. Migrations 0001-0008 are not modified.

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and the backend Pydantic models)
-- ---------------------------------------------------------------------------
create type public.audit_actor_type as enum ('USER', 'OPERATOR');
create type public.audit_action as enum (
    'JOB_RETRY', 'JOB_RESUME_ATTRIBUTION', 'CANDIDATE_APPROVE', 'CANDIDATE_MERGE', 'CANDIDATE_REJECT',
    'COURSE_MEMBER_ADD', 'ROLE_CHANGE');
create type public.benchmark_mode as enum ('DETERMINISTIC', 'REPLAY', 'LIVE');
create type public.benchmark_verdict as enum ('PASS', 'FAIL');

-- ---------------------------------------------------------------------------
-- audit_events (§7.1)
-- ---------------------------------------------------------------------------
create table public.audit_events (
    id                uuid primary key default gen_random_uuid(),
    -- USER: an authenticated admin through the API. OPERATOR: a server-side script run by the
    -- operator over DATABASE_URL (e.g. a role change), with no account behind it.
    actor_type        public.audit_actor_type not null,
    actor_id          uuid references public.profiles (id) on delete set null,
    -- The actor's profile role when acting (read from profiles, never from the token).
    actor_role        public.app_role,
    action            public.audit_action not null,
    entity_type       text not null check (entity_type ~ '^[a-z][a-z0-9_]{2,63}$'),
    entity_id         uuid not null,
    -- The mutation's effect: ids, states and codes only.
    metadata          jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    -- Idempotency: the request's Idempotency-Key and a hash of the request first sent with it.
    client_request_id text check (client_request_id is null or client_request_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
    request_hash      text check (request_hash is null or request_hash ~ '^[0-9a-f]{64}$'),
    created_at        timestamptz not null default now(),
    constraint audit_events_actor_shape check (
        (actor_type = 'USER' and actor_role is not null and client_request_id is not null)
        or (actor_type = 'OPERATOR' and actor_id is null and actor_role is null)),
    constraint audit_events_request_shape check ((client_request_id is null) = (request_hash is null))
);

comment on table public.audit_events is
    'Admin and operator mutations (P7): who did what to which entity, with the request''s Idempotency-Key. Server-only, append-only.';

-- A retried admin request (same Idempotency-Key) is the same mutation.
create unique index audit_events_request_key
    on public.audit_events (actor_id, client_request_id) where client_request_id is not null;
create index audit_events_entity_idx on public.audit_events (entity_type, entity_id, created_at desc);
create index audit_events_created_idx on public.audit_events (created_at desc);

-- Append-only, except that deleting the actor's account nulls the reference (ON DELETE SET NULL).
create function public.audit_events_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.actor_id is null and (to_jsonb(new) - 'actor_id') = (to_jsonb(old) - 'actor_id') then
        return new;
    end if;
    raise exception 'audit_events is append-only' using errcode = '55000';
end;
$$;

create trigger audit_events_append_only
    before update on public.audit_events
    for each row execute function public.audit_events_guard_update();

revoke execute on function public.audit_events_guard_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- benchmark_runs (§17; written by P8)
-- ---------------------------------------------------------------------------
create table public.benchmark_runs (
    id                 uuid primary key default gen_random_uuid(),
    set_name           text not null check (set_name ~ '^[a-z0-9][a-z0-9._-]{1,63}$'),
    set_version        text not null check (set_version ~ '^[a-z0-9][a-z0-9._/-]{0,63}$'),
    mode               public.benchmark_mode not null,
    -- The model under test. Deterministic runs feed scripted outputs (no model); replay runs
    -- name the model whose recorded answers they replay.
    provider           text check (provider is null or provider ~ '^[a-z][a-z0-9_-]{1,31}$'),
    model              text check (model is null or char_length(model) between 1 and 100),
    routing            text check (routing is null or routing ~ '^[a-z0-9][a-z0-9._-]{1,63}$'),
    prompt_versions    jsonb not null default '{}'::jsonb check (jsonb_typeof(prompt_versions) = 'object'),
    -- sha256 of the policy_config values the run used, and the commit it ran on.
    policy_hash        text not null check (policy_hash ~ '^[0-9a-f]{64}$'),
    code_sha           text check (code_sha is null or code_sha ~ '^[0-9a-f]{7,40}$'),
    case_count         integer not null check (case_count >= 0),
    passed_count       integer not null check (passed_count >= 0),
    failed_count       integer not null check (failed_count >= 0),
    -- Cases that could not run (reported, never counted as passed).
    blocked_count      integer not null default 0 check (blocked_count >= 0),
    -- Zero-tolerance gates: {"<gate>": {"value": .., "threshold": .., "pass": bool}}.
    hard_gates         jsonb not null check (jsonb_typeof(hard_gates) = 'object'),
    -- Calibration targets (not invariants): {"<metric>": {"value": .., "target": .., "met": bool}}.
    metrics            jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
    verdict            public.benchmark_verdict not null,
    -- Provider requests the run spent (generation / embedding). Deterministic and replay: 0.
    provider_requests  integer not null default 0 check (provider_requests >= 0),
    embedding_requests integer not null default 0 check (embedding_requests >= 0),
    -- Per-family summary and the ids of failing or blocked cases. Ids and codes only.
    report             jsonb not null default '{}'::jsonb check (jsonb_typeof(report) = 'object'),
    started_at         timestamptz not null,
    finished_at        timestamptz not null,
    created_at         timestamptz not null default now(),
    constraint benchmark_runs_counts check (passed_count + failed_count + blocked_count = case_count),
    constraint benchmark_runs_timing check (finished_at >= started_at),
    constraint benchmark_runs_offline_requests check (
        mode = 'LIVE' or (provider_requests = 0 and embedding_requests = 0)),
    constraint benchmark_runs_model_named check (
        mode = 'DETERMINISTIC' or (provider is not null and model is not null))
);

comment on table public.benchmark_runs is
    'Benchmark runs (P8, architecture §17): mode, model, hard gates, calibration metrics and verdict. Server-only, append-only.';

create index benchmark_runs_set_idx on public.benchmark_runs (set_name, mode, created_at desc);

create trigger benchmark_runs_append_only
    before update on public.benchmark_runs
    for each row execute function public.reject_update();

-- ---------------------------------------------------------------------------
-- processing_jobs: manual retry bookkeeping (§13 POST /v1/admin/jobs/{id}/retry)
-- ---------------------------------------------------------------------------
alter table public.processing_jobs
    add column manual_retry_count integer not null default 0 check (manual_retry_count between 0 and 5),
    add column last_manual_retry_at timestamptz,
    add constraint processing_jobs_manual_retry_shape check (
        (manual_retry_count = 0) = (last_manual_retry_at is null));

comment on column public.processing_jobs.manual_retry_count is
    'Manual retries by an admin (at most 5). The retrying admin is recorded in audit_events.';

-- The worker never touches the manual retry fields. A manual retry adds exactly one, only to a
-- FAILED job or to a COMPLETED raw-message job resuming its pending attribution, and re-queues it
-- as PENDING with a fresh attempt budget.
create function public.processing_jobs_guard_manual_retry()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.manual_retry_count = old.manual_retry_count then
        if new.last_manual_retry_at is distinct from old.last_manual_retry_at then
            raise exception 'manual retry metadata changes only with a manual retry' using errcode = '55000';
        end if;
        return new;
    end if;
    if new.manual_retry_count <> old.manual_retry_count + 1 then
        raise exception 'a manual retry counts exactly one' using errcode = '55000';
    end if;
    if not (old.state = 'FAILED' or (old.state = 'COMPLETED' and old.job_type = 'PROCESS_RAW_MESSAGE')) then
        raise exception 'job % is %: only a FAILED job (or a COMPLETED raw-message job resuming its attribution) is retried manually',
            old.id, old.state using errcode = '55000';
    end if;
    if new.state <> 'PENDING' or new.attempts <> 0 or new.locked_at is not null
       or new.last_manual_retry_at is null then
        raise exception 'a manual retry re-queues the job as PENDING with a fresh attempt budget' using errcode = '55000';
    end if;
    return new;
end;
$$;

create trigger processing_jobs_guard_manual_retry
    before update on public.processing_jobs
    for each row execute function public.processing_jobs_guard_manual_retry();

revoke execute on function public.processing_jobs_guard_manual_retry() from public, anon, authenticated;

-- Admin job lists: by state and recency, and the failed ones.
create index processing_jobs_state_updated_idx on public.processing_jobs (state, updated_at desc);
create index processing_jobs_failed_idx on public.processing_jobs (updated_at desc) where state = 'FAILED';

-- ---------------------------------------------------------------------------
-- skill_candidates: review (§8.2 "review/canonicalization approves or merges a candidate")
-- ---------------------------------------------------------------------------
alter table public.skill_candidates
    add column reviewed_by uuid references public.profiles (id) on delete set null,
    add column reviewed_at timestamptz,
    add column review_note text check (review_note is null or char_length(btrim(review_note)) between 1 and 1000),
    add constraint skill_candidates_review_shape check ((status = 'PENDING_REVIEW') = (reviewed_at is null)),
    add constraint skill_candidates_pending_unreviewed check (
        status <> 'PENDING_REVIEW' or (reviewed_by is null and review_note is null));

-- A rejected name has one row; later proposals of it count there (never a new PENDING row).
create unique index skill_candidates_rejected_key
    on public.skill_candidates (normalized_name) where status = 'REJECTED';
create index skill_candidates_status_idx on public.skill_candidates (status, updated_at desc);

-- PENDING_REVIEW -> APPROVED / MERGED / REJECTED once, by an ADMIN. APPROVED names a node created
-- by the approval, MERGED an existing node; either way the candidate's name must now resolve to
-- that ACTIVE assessable skill. A reviewed candidate is final: only its occurrence count grows
-- (and ON DELETE SET NULL of its references is allowed).
create function public.skill_candidates_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    changed text[];
begin
    select coalesce(array_agg(n.key order by n.key), '{}')
      into changed
      from jsonb_each(to_jsonb(new)) n
      join jsonb_each(to_jsonb(old)) o using (key)
     where n.value is distinct from o.value and n.key not in ('updated_at', 'occurrences');
    if new.occurrences < old.occurrences then
        raise exception 'candidate occurrences never decrease' using errcode = '55000';
    end if;
    if changed <@ array['first_course_id', 'reviewed_by']
       and (new.first_course_id is null or new.first_course_id = old.first_course_id)
       and (new.reviewed_by is null or new.reviewed_by = old.reviewed_by) then
        return new;
    end if;
    if old.status <> 'PENDING_REVIEW' then
        raise exception 'skill candidate % is % and final', old.id, old.status using errcode = '55000';
    end if;
    if new.status = 'PENDING_REVIEW' then
        if 'normalized_name' = any (changed) then
            raise exception 'a candidate''s name key is immutable' using errcode = '55000';
        end if;
        return new;
    end if;
    if not changed <@ array['status', 'resolved_skill_id', 'reviewed_by', 'reviewed_at', 'review_note'] then
        raise exception 'a candidate review changes only its status, resolution and review fields (%)',
            array(select unnest(changed) except select unnest(array['status', 'resolved_skill_id', 'reviewed_by',
                                                                     'reviewed_at', 'review_note']))
            using errcode = '55000';
    end if;
    if not exists (select 1 from public.profiles p where p.id = new.reviewed_by and p.role = 'ADMIN') then
        raise exception 'a candidate is reviewed by an ADMIN' using errcode = '23514';
    end if;
    if new.status in ('APPROVED', 'MERGED') and not exists (
        select 1 from public.skill_aliases a
          join public.skill_nodes n on n.id = a.skill_id
         where a.normalized_alias = new.normalized_name and a.skill_id = new.resolved_skill_id
           and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')
           and (new.status = 'MERGED' or n.source = 'CANDIDATE_APPROVAL')) then
        raise exception 'the % candidate''s name must resolve to its ACTIVE assessable skill %',
            new.status, new.resolved_skill_id using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger skill_candidates_guard_update
    before update on public.skill_candidates
    for each row execute function public.skill_candidates_guard_update();

revoke execute on function public.skill_candidates_guard_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- course_memberships: teacher membership validation (§4)
-- ---------------------------------------------------------------------------
create function public.course_memberships_guard_teacher()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.role = 'TEACHER' and not exists (
        select 1 from public.profiles p where p.id = new.user_id and p.role in ('TEACHER', 'ADMIN')) then
        raise exception 'a TEACHER membership needs a TEACHER or ADMIN profile (user %)', new.user_id
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger course_memberships_guard_teacher
    before insert or update of role, user_id on public.course_memberships
    for each row execute function public.course_memberships_guard_teacher();

-- Keeps the invariant from the other side: a teacher is demoted only after leaving their courses.
create function public.profiles_guard_teacher_role()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.role = 'STUDENT' and old.role <> 'STUDENT' and exists (
        select 1 from public.course_memberships m where m.user_id = new.id and m.role = 'TEACHER') then
        raise exception 'user % still has TEACHER memberships: remove them before the role becomes STUDENT', new.id
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger profiles_guard_teacher_role
    before update of role on public.profiles
    for each row execute function public.profiles_guard_teacher_role();

revoke execute on function public.course_memberships_guard_teacher() from public, anon, authenticated;
revoke execute on function public.profiles_guard_teacher_role() from public, anon, authenticated;

-- Cohort and teacher lookups.
create index course_memberships_course_role_idx on public.course_memberships (course_id, role);

-- The daily stale-ledger recompute (P8) finds rows computed before the current UTC day.
create index skill_ledger_computed_as_of_idx on public.skill_ledger (computed_as_of);

-- ---------------------------------------------------------------------------
-- policy_config: the teacher overview (engineering defaults, ADR 0008)
-- ---------------------------------------------------------------------------
insert into public.policy_config (key, value, description) values
    ('teacher_view',
     '{"min_cohort": 3, "window_days": 30, "top_n": 10}',
     'Teacher course overview (P7): aggregates of STUDENT members only, suppressed below min_cohort; common mapped skills over the last window_days; at most top_n rows per list. No per-student rows, names, AI-usage counts or debt scores.');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.audit_events enable row level security;
alter table public.benchmark_runs enable row level security;

-- Server-only: no client privilege and no policy.
revoke all on table public.audit_events, public.benchmark_runs from anon, authenticated;
