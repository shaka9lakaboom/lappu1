-- 0003_courses_skill_graph.sql
-- SkillMirror P2 (courses + skill graph) and P3A (qualification + retrieval +
-- mapping) persistence. Architecture §7.1, §8, §9.1-§9.4, §14, Appendix A/B.
--
-- * Global skill registry (skill_nodes / skill_aliases / skill_edges) is the
--   canonical identity layer. A skill's UUID never changes; renames and merges
--   keep the UUID and record the old names as aliases.
-- * course_skills is the per-course overlay onto the registry.
-- * skill_embeddings (pgvector, 768 dims) + a tsvector search document back the
--   two-channel candidate retrieval.
-- * model_runs logs every model call; policy_config holds tunable defaults.
-- * activity_segments / mapping_decisions / skill_mappings / skill_candidates
--   trace qualification and mapping back to raw_messages. There are NO evidence,
--   ledger, debt or verification tables here (P3B/P4).
--
-- Only the backend (table owner / service connection) writes. Browser clients
-- get SELECT on their own course and analysis rows and on ACTIVE registry rows.

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and the backend Pydantic models)
-- ---------------------------------------------------------------------------
create type public.course_status as enum ('ACTIVE', 'ARCHIVED');
create type public.course_graph_status as enum ('PENDING', 'GENERATING', 'EMBEDDING', 'READY', 'FAILED');
create type public.course_member_role as enum ('STUDENT', 'TEACHER');
create type public.skill_node_kind as enum ('DOMAIN', 'SUBJECT', 'TOPIC', 'SKILL', 'SUBSKILL');
create type public.skill_status as enum ('ACTIVE', 'CANDIDATE', 'DEPRECATED', 'MERGED');
create type public.skill_source as enum ('COURSE_BOOTSTRAP', 'CANDIDATE_APPROVAL', 'MANUAL', 'SEED');
create type public.skill_edge_type as enum ('PARENT', 'PREREQUISITE', 'RELATED');
create type public.skill_alias_kind as enum ('CANONICAL', 'VARIANT', 'ABBREVIATION', 'PREVIOUS_NAME', 'MERGED_NAME');
create type public.model_run_status as enum ('SUCCEEDED', 'INVALID_OUTPUT', 'FAILED', 'TIMEOUT', 'RATE_LIMITED', 'UNAVAILABLE');
create type public.segment_context as enum ('academic', 'professional', 'personal', 'entertainment', 'administrative', 'unknown');
create type public.segment_intent as enum ('learn', 'understand', 'practice', 'solve', 'delegate', 'lookup', 'create', 'transform', 'communicate', 'other');
create type public.learning_relevance as enum ('high', 'medium', 'low', 'none', 'uncertain');
create type public.segment_route as enum ('MAP', 'METADATA_ONLY', 'STOP', 'UNCERTAIN');
create type public.mapping_outcome as enum ('MAPPED', 'ABSTAINED');
create type public.skill_mapping_status as enum ('ACCEPTED', 'ABSTAINED', 'REJECTED');
create type public.skill_candidate_status as enum ('PENDING_REVIEW', 'APPROVED', 'MERGED', 'REJECTED');

-- Helpers that RLS policies call live in a schema the Data API does not expose.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

-- Generic append-only guard for derived provenance tables.
create function public.reject_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception '% is append-only; insert a new analysis version instead', tg_table_name
        using errcode = '55000';
end;
$$;
revoke execute on function public.reject_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- model_runs: every model call (architecture §14)
-- ---------------------------------------------------------------------------
create table public.model_runs (
    id                uuid primary key default gen_random_uuid(),
    trace_id          text not null check (char_length(trace_id) between 1 and 128),
    task_type         text not null check (task_type ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    provider          text not null check (provider ~ '^[a-z][a-z0-9_-]{1,31}$'),
    model             text not null check (char_length(model) between 1 and 100),
    -- A model call without a prompt version is invalid (architecture §18.1).
    prompt_version    text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    input_hash        text not null check (input_hash ~ '^[0-9a-f]{64}$'),
    output_hash       text check (output_hash is null or output_hash ~ '^[0-9a-f]{64}$'),
    -- Validated, parsed output (never the prompt). Null for failures and embeddings.
    output            jsonb,
    input_tokens      integer check (input_tokens is null or input_tokens >= 0),
    output_tokens     integer check (output_tokens is null or output_tokens >= 0),
    total_tokens      integer check (total_tokens is null or total_tokens >= 0),
    latency_ms        integer not null check (latency_ms >= 0),
    status            public.model_run_status not null,
    attempt           smallint not null default 1 check (attempt between 1 and 5),
    repair_of_id      uuid references public.model_runs (id),
    error_code        text check (error_code is null or char_length(error_code) <= 64),
    error_message     text check (error_message is null or char_length(error_message) <= 2000),
    learner_id        uuid references public.profiles (id) on delete set null,
    course_id         uuid,
    processing_job_id uuid,
    created_at        timestamptz not null default now()
);

comment on table public.model_runs is 'One row per model call: task, model, prompt version, usage, latency, status (architecture §14).';

create index model_runs_trace_idx on public.model_runs (trace_id);
create index model_runs_task_created_idx on public.model_runs (task_type, created_at desc);
create index model_runs_failures_idx on public.model_runs (created_at desc) where status <> 'SUCCEEDED';

create trigger model_runs_append_only
    before update on public.model_runs
    for each row execute function public.reject_update();

-- ---------------------------------------------------------------------------
-- policy_config: tunable intelligence defaults (architecture §5.2, Appendix B)
-- ---------------------------------------------------------------------------
create table public.policy_config (
    id          uuid primary key default gen_random_uuid(),
    scope_type  text not null default 'global' check (scope_type in ('global', 'course', 'skill')),
    scope_id    uuid,
    key         text not null check (key ~ '^[a-z][a-z0-9_.]{2,63}$'),
    value       jsonb not null check (jsonb_typeof(value) = 'object'),
    version     integer not null default 1 check (version >= 1),
    description text check (description is null or char_length(description) <= 500),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint policy_config_scope_consistency check ((scope_type = 'global') = (scope_id is null)),
    constraint policy_config_scope_key unique nulls not distinct (scope_type, scope_id, key)
);

comment on table public.policy_config is 'Rules and thresholds. Engineering defaults tuned against the benchmark, not hardcoded.';

create trigger policy_config_set_updated_at
    before update on public.policy_config
    for each row execute function public.set_updated_at();

-- Appendix B / §9.3 / §9.4 engineering defaults.
insert into public.policy_config (key, value, description) values
    ('retrieval',
     '{"weights": {"semantic": 0.55, "lexical": 0.30, "course_prior": 0.15},
       "pool_size": 20, "rerank_size": 8, "channel_limit": 40, "query_max_chars": 4000}',
     'Two-stage candidate retrieval (§9.3): score weights, top-20 pool, top-8 rerank.'),
    ('mapping',
     '{"accept_threshold": 0.80, "adjudicate_min": 0.65, "max_skills_per_segment": 5}',
     'Mapping confidence gate (§9.4): >=0.80 accept, 0.65-0.79 second pass, <0.65 abstain.'),
    ('qualification',
     '{"learning_relevance_levels": ["high", "medium"], "min_relevance_confidence": 0.60,
       "min_skill_bearing_confidence": 0.60, "max_segments": 4}',
     'Relevance / skill-bearing routing (§9.2). Below the confidence floors a segment is UNCERTAIN.'),
    ('processing_unit',
     '{"recent_context_messages": 4, "recent_context_max_chars": 4000, "unit_max_chars": 12000,
       "pairing_window_seconds": 120, "pairing_retry_seconds": 20}',
     'Turn-pair processing unit with bounded recent context (§9.1).'),
    ('skill_graph',
     '{"target_min_skills": 30, "target_max_skills": 60, "hard_min_skills": 20,
       "hard_max_skills": 80, "default_importance": 0.5}',
     'Course bootstrap (§8.2): ~30-60 assessable skills; importance defaults to 0.5.');

-- ---------------------------------------------------------------------------
-- courses and memberships
-- ---------------------------------------------------------------------------
create table public.courses (
    id                 uuid primary key default gen_random_uuid(),
    owner_id           uuid not null references public.profiles (id) on delete cascade,
    name               text not null check (char_length(btrim(name)) between 1 and 200),
    subject            text check (subject is null or char_length(btrim(subject)) between 1 and 120),
    level              text check (level is null or char_length(btrim(level)) between 1 and 60),
    description        text check (description is null or char_length(description) <= 4000),
    status             public.course_status not null default 'ACTIVE',
    graph_status       public.course_graph_status not null default 'PENDING',
    graph_version      integer not null default 0 check (graph_version >= 0),
    graph_error        text check (graph_error is null or char_length(graph_error) <= 2000),
    graph_generated_at timestamptz,
    graph_model_run_id uuid references public.model_runs (id),
    -- Idempotency-Key of the creating request: a retried POST returns the same course.
    client_request_id  text check (client_request_id is null or client_request_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    constraint courses_owner_request_key unique (owner_id, client_request_id)
);

comment on table public.courses is 'Learning context (architecture §7.1). graph_* tracks the skill-graph bootstrap.';

create index courses_owner_idx on public.courses (owner_id, created_at desc);

create trigger courses_set_updated_at
    before update on public.courses
    for each row execute function public.set_updated_at();

alter table public.model_runs
    add constraint model_runs_course_fk foreign key (course_id) references public.courses (id) on delete set null;

create table public.course_memberships (
    course_id  uuid not null references public.courses (id) on delete cascade,
    user_id    uuid not null references public.profiles (id) on delete cascade,
    role       public.course_member_role not null default 'STUDENT',
    created_at timestamptz not null default now(),
    primary key (course_id, user_id)
);

create index course_memberships_user_idx on public.course_memberships (user_id);

-- SECURITY DEFINER so course/overlay policies do not recurse through the
-- memberships policy. Lives in `private`, which the Data API does not expose.
create function private.is_course_member(p_course_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.course_memberships m
         where m.course_id = p_course_id and m.user_id = (select auth.uid())
    );
$$;
revoke execute on function private.is_course_member(uuid) from public, anon;
grant execute on function private.is_course_member(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- Global skill registry
-- ---------------------------------------------------------------------------
create table public.skill_nodes (
    id                  uuid primary key default gen_random_uuid(),
    slug                text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and char_length(slug) <= 120),
    canonical_name      text not null check (char_length(btrim(canonical_name)) between 2 and 160),
    -- Canonical key (backend skill_key(): case, punctuation, spelling variants).
    normalized_name     text not null check (normalized_name = lower(normalized_name)
                                             and normalized_name = btrim(normalized_name)
                                             and char_length(normalized_name) between 1 and 160),
    description         text not null check (char_length(btrim(description)) between 8 and 600),
    node_kind           public.skill_node_kind not null default 'SKILL',
    status              public.skill_status not null default 'ACTIVE',
    version             integer not null default 1 check (version >= 1),
    difficulty_band     smallint check (difficulty_band is null or difficulty_band between 1 and 5),
    assessment_types    text[] not null default '{}'
                        check (assessment_types <@ array['mcq', 'numeric', 'code', 'sql', 'short_response', 'reasoning']),
    merged_into_id      uuid references public.skill_nodes (id),
    source              public.skill_source not null,
    source_course_id    uuid references public.courses (id) on delete set null,
    source_model_run_id uuid references public.model_runs (id),
    search_document     tsvector not null default ''::tsvector,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    constraint skill_nodes_merge_consistency check ((status = 'MERGED') = (merged_into_id is not null)),
    constraint skill_nodes_not_merged_into_self check (merged_into_id is null or merged_into_id <> id)
);

comment on table public.skill_nodes is
    'Canonical skill registry (architecture §8). The UUID is immutable; names are not.';

create unique index skill_nodes_normalized_name_key on public.skill_nodes (normalized_name) where status <> 'MERGED';
create index skill_nodes_search_idx on public.skill_nodes using gin (search_document);
create index skill_nodes_active_kind_idx on public.skill_nodes (node_kind) where status = 'ACTIVE';

create table public.skill_aliases (
    id               uuid primary key default gen_random_uuid(),
    skill_id         uuid not null references public.skill_nodes (id),
    alias            text not null check (char_length(btrim(alias)) between 1 and 160),
    normalized_alias text not null check (normalized_alias = lower(normalized_alias)
                                          and normalized_alias = btrim(normalized_alias)
                                          and char_length(normalized_alias) between 1 and 160),
    locale           text not null default 'en' check (locale ~ '^[a-z]{2}(-[A-Z]{2})?$'),
    alias_kind       public.skill_alias_kind not null default 'VARIANT',
    source           public.skill_source not null,
    created_at       timestamptz not null default now(),
    -- One canonical key maps to exactly one skill UUID, across all locales.
    constraint skill_aliases_normalized_key unique (normalized_alias)
);

comment on table public.skill_aliases is 'Search/canonicalization aliases. Each normalized alias resolves to one skill UUID.';

create index skill_aliases_skill_idx on public.skill_aliases (skill_id);

create table public.skill_edges (
    id            uuid primary key default gen_random_uuid(),
    -- PARENT: from is the parent of to. PREREQUISITE: from is required for to.
    -- RELATED: symmetric, stored once with from < to.
    from_skill_id uuid not null references public.skill_nodes (id),
    to_skill_id   uuid not null references public.skill_nodes (id),
    edge_type     public.skill_edge_type not null,
    weight        real not null default 1.0 check (weight > 0 and weight <= 1),
    source        public.skill_source not null,
    course_id     uuid references public.courses (id) on delete set null,
    created_at    timestamptz not null default now(),
    constraint skill_edges_no_self check (from_skill_id <> to_skill_id),
    constraint skill_edges_related_ordered check (edge_type <> 'RELATED' or from_skill_id < to_skill_id),
    constraint skill_edges_unique unique (from_skill_id, to_skill_id, edge_type)
);

create index skill_edges_to_idx on public.skill_edges (to_skill_id, edge_type);

create table public.course_skills (
    course_id     uuid not null references public.courses (id) on delete cascade,
    skill_id      uuid not null references public.skill_nodes (id),
    importance    real not null default 0.5 check (importance >= 0 and importance <= 1),
    source        public.skill_source not null,
    active        boolean not null default true,
    graph_version integer not null check (graph_version >= 1),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    primary key (course_id, skill_id)
);

comment on table public.course_skills is 'Course overlay onto the global registry (architecture §8.2).';

create index course_skills_skill_idx on public.course_skills (skill_id);

create trigger course_skills_set_updated_at
    before update on public.course_skills
    for each row execute function public.set_updated_at();

create table public.skill_embeddings (
    id            uuid primary key default gen_random_uuid(),
    skill_id      uuid not null references public.skill_nodes (id) on delete cascade,
    model         text not null check (char_length(model) between 1 and 100),
    -- skill_nodes.version at embedding time + hash of the exact embedded text:
    -- an unchanged node is never re-embedded (architecture §14.3).
    graph_version integer not null check (graph_version >= 1),
    content_hash  text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    input_version text not null check (input_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    embedding     extensions.vector(768) not null,
    model_run_id  uuid references public.model_runs (id),
    created_at    timestamptz not null default now(),
    constraint skill_embeddings_skill_model_key unique (skill_id, model)
);

comment on table public.skill_embeddings is 'Retrieval index: one current 768-d embedding per skill and model (architecture §9.3).';

create index skill_embeddings_hnsw_idx
    on public.skill_embeddings using hnsw (embedding extensions.vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Registry triggers: immutable identity, rename -> alias, search document
-- ---------------------------------------------------------------------------
create function public.skill_search_document(p_skill_id uuid, p_name text, p_description text)
returns tsvector
language sql
stable
set search_path = ''
as $$
    select setweight(to_tsvector('english'::regconfig, coalesce(p_name, '')), 'A')
        || setweight(to_tsvector('english'::regconfig, coalesce((
               select string_agg(a.alias, ' ' order by a.alias)
                 from public.skill_aliases a
                where a.skill_id = p_skill_id and a.alias_kind <> 'CANONICAL'), '')), 'B')
        || setweight(to_tsvector('english'::regconfig, coalesce(p_description, '')), 'C');
$$;

create function public.skill_nodes_before_write()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if new.id <> old.id then
            raise exception 'skill_nodes.id is immutable (rename or merge instead)' using errcode = '55000';
        end if;
        if new.canonical_name is distinct from old.canonical_name
           or new.description is distinct from old.description then
            new.version := old.version + 1;
        end if;
        new.updated_at := now();
    end if;
    new.search_document := public.skill_search_document(new.id, new.canonical_name, new.description);
    return new;
end;
$$;

create trigger skill_nodes_before_write
    before insert or update on public.skill_nodes
    for each row execute function public.skill_nodes_before_write();

-- The canonical name is itself an alias row, so one lookup resolves any name.
-- A rename keeps the UUID and demotes the old canonical name to PREVIOUS_NAME.
create function public.skill_nodes_sync_canonical_alias()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    owner uuid;
begin
    if tg_op = 'UPDATE' then
        if new.normalized_name is not distinct from old.normalized_name
           and new.canonical_name is not distinct from old.canonical_name then
            return null;
        end if;
        update public.skill_aliases
           set alias_kind = 'PREVIOUS_NAME'
         where skill_id = new.id and alias_kind = 'CANONICAL';
    end if;

    select skill_id into owner from public.skill_aliases where normalized_alias = new.normalized_name;
    if owner is null then
        insert into public.skill_aliases (skill_id, alias, normalized_alias, alias_kind, source)
        values (new.id, new.canonical_name, new.normalized_name, 'CANONICAL', new.source);
    elsif owner = new.id then
        update public.skill_aliases
           set alias_kind = 'CANONICAL', alias = new.canonical_name
         where normalized_alias = new.normalized_name;
    else
        raise exception 'canonical name "%" is already an alias of skill %', new.canonical_name, owner
            using errcode = '23505';
    end if;
    return null;
end;
$$;

create trigger skill_nodes_sync_canonical_alias
    after insert or update of canonical_name, normalized_name on public.skill_nodes
    for each row execute function public.skill_nodes_sync_canonical_alias();

create function public.skill_aliases_refresh_search()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op in ('UPDATE', 'DELETE') then
        update public.skill_nodes n
           set search_document = public.skill_search_document(n.id, n.canonical_name, n.description)
         where n.id = old.skill_id;
    end if;
    if tg_op in ('INSERT', 'UPDATE') then
        update public.skill_nodes n
           set search_document = public.skill_search_document(n.id, n.canonical_name, n.description)
         where n.id = new.skill_id;
    end if;
    return null;
end;
$$;

create trigger skill_aliases_refresh_search
    after insert or update or delete on public.skill_aliases
    for each row execute function public.skill_aliases_refresh_search();

revoke execute on function public.skill_nodes_before_write() from public, anon, authenticated;
revoke execute on function public.skill_nodes_sync_canonical_alias() from public, anon, authenticated;
revoke execute on function public.skill_aliases_refresh_search() from public, anon, authenticated;
revoke execute on function public.skill_search_document(uuid, text, text) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- P3A: qualification, retrieval and mapping provenance
-- ---------------------------------------------------------------------------
-- One row per task unit of an analysed turn (§9.1, Appendix A.1). The unit is
-- keyed by its anchor raw message (the user message, or the assistant message
-- for an orphan turn) and the analysis version, so a job replay is a no-op.
create table public.activity_segments (
    id                         uuid primary key default gen_random_uuid(),
    learner_id                 uuid not null,
    conversation_id            uuid not null,
    anchor_message_id          uuid not null,
    user_message_id            uuid,
    assistant_message_id       uuid,
    source_message_ids         uuid[] not null check (cardinality(source_message_ids) between 1 and 2),
    context_message_ids        uuid[] not null default '{}',
    segment_index              smallint not null check (segment_index between 0 and 19),
    segment_count              smallint not null check (segment_count between 1 and 20),
    text                       text not null check (char_length(text) between 1 and 20000),
    context                    public.segment_context,
    intent                     public.segment_intent,
    learning_relevance         public.learning_relevance,
    relevance_confidence       real check (relevance_confidence is null or relevance_confidence between 0 and 1),
    skill_bearing              boolean,
    skill_bearing_confidence   real check (skill_bearing_confidence is null or skill_bearing_confidence between 0 and 1),
    reason_code                text not null check (reason_code ~ '^[A-Za-z][A-Za-z0-9_]{1,63}$'),
    route                      public.segment_route not null,
    route_reason               text not null check (route_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    context_incomplete         boolean not null,
    course_ids                 uuid[] not null default '{}',
    qualification_model_run_id uuid references public.model_runs (id),
    prompt_version             text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    analysis_version           text not null check (analysis_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    processing_job_id          uuid,
    created_at                 timestamptz not null default now(),
    constraint activity_segments_index_in_range check (segment_index < segment_count),
    constraint activity_segments_unit_key unique (anchor_message_id, analysis_version, segment_index),
    constraint activity_segments_id_learner_key unique (id, learner_id),
    constraint activity_segments_conversation_fk foreign key (conversation_id, learner_id)
        references public.conversations (id, learner_id) on delete cascade,
    constraint activity_segments_anchor_fk foreign key (anchor_message_id, learner_id)
        references public.raw_messages (id, learner_id) on delete cascade,
    constraint activity_segments_user_message_fk foreign key (user_message_id, learner_id)
        references public.raw_messages (id, learner_id) on delete cascade,
    constraint activity_segments_assistant_message_fk foreign key (assistant_message_id, learner_id)
        references public.raw_messages (id, learner_id) on delete cascade,
    constraint activity_segments_anchor_in_sources check (anchor_message_id = any (source_message_ids))
);

comment on table public.activity_segments is
    'Qualified task units with relevance/intent/skill-bearing results and routing (P3A). Append-only.';

create index activity_segments_learner_idx on public.activity_segments (learner_id, created_at desc);
create index activity_segments_sources_idx on public.activity_segments using gin (source_message_ids);

create trigger activity_segments_append_only
    before update on public.activity_segments
    for each row execute function public.reject_update();

-- NEW_SKILL_CANDIDATE proposals (§8.2, Appendix A.2). Never production-visible:
-- review/canonicalization approves or merges a candidate before it becomes a node.
create table public.skill_candidates (
    id                  uuid primary key default gen_random_uuid(),
    canonical_name      text not null check (char_length(btrim(canonical_name)) between 2 and 160),
    normalized_name     text not null check (normalized_name = lower(normalized_name)
                                             and char_length(normalized_name) between 1 and 160),
    description         text check (description is null or char_length(description) <= 600),
    parent_candidate_id uuid references public.skill_nodes (id),
    status              public.skill_candidate_status not null default 'PENDING_REVIEW',
    resolved_skill_id   uuid references public.skill_nodes (id),
    occurrences         integer not null default 1 check (occurrences >= 1),
    first_course_id     uuid references public.courses (id) on delete set null,
    first_model_run_id  uuid references public.model_runs (id),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    constraint skill_candidates_resolution check (
        (status in ('APPROVED', 'MERGED')) = (resolved_skill_id is not null))
);

create unique index skill_candidates_pending_key
    on public.skill_candidates (normalized_name) where status = 'PENDING_REVIEW';

create trigger skill_candidates_set_updated_at
    before update on public.skill_candidates
    for each row execute function public.set_updated_at();

-- One decision per MAP-routed segment: retrieval pool, reranked mapper input,
-- model runs and outcome (§9.3-§9.4, Appendix A.2).
create table public.mapping_decisions (
    id                       uuid primary key default gen_random_uuid(),
    segment_id               uuid not null unique,
    learner_id               uuid not null,
    outcome                  public.mapping_outcome not null,
    abstain_reason           text check (abstain_reason is null or abstain_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    -- Top-N retrieval pool with per-channel scores (skill_id, name, semantic, lexical, prior, score, in_course).
    retrieval_candidates     jsonb not null check (jsonb_typeof(retrieval_candidates) = 'array'),
    -- Reranked top-K skill ids supplied to the mapper, in order. Mappings must come from here.
    mapper_candidate_ids     uuid[] not null default '{}',
    rerank_fallback          boolean not null default false,
    query_model_run_id       uuid references public.model_runs (id),
    rerank_model_run_id      uuid references public.model_runs (id),
    mapping_model_run_id     uuid references public.model_runs (id),
    adjudication_model_run_id uuid references public.model_runs (id),
    prompt_versions          jsonb not null check (jsonb_typeof(prompt_versions) = 'object'),
    policy_snapshot          jsonb not null check (jsonb_typeof(policy_snapshot) = 'object'),
    new_skill_candidate_id   uuid references public.skill_candidates (id),
    mapper_version           text not null check (mapper_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    created_at               timestamptz not null default now(),
    constraint mapping_decisions_abstain_reason check ((outcome = 'ABSTAINED') = (abstain_reason is not null)),
    constraint mapping_decisions_id_learner_key unique (id, learner_id),
    constraint mapping_decisions_segment_fk foreign key (segment_id, learner_id)
        references public.activity_segments (id, learner_id) on delete cascade
);

create index mapping_decisions_learner_idx on public.mapping_decisions (learner_id, created_at desc);

create trigger mapping_decisions_append_only
    before update on public.mapping_decisions
    for each row execute function public.reject_update();

-- Per-skill mapping result (architecture §7.1 skill_mappings).
create table public.skill_mappings (
    id                    uuid primary key default gen_random_uuid(),
    decision_id           uuid not null,
    segment_id            uuid not null,
    learner_id            uuid not null,
    skill_id              uuid not null references public.skill_nodes (id),
    status                public.skill_mapping_status not null,
    confidence            real not null check (confidence between 0 and 1),
    first_pass_confidence real not null check (first_pass_confidence between 0 and 1),
    adjudicated           boolean not null default false,
    reason_code           text not null check (reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    status_reason         text not null check (status_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    evidence_span         text check (evidence_span is null or char_length(evidence_span) <= 2000),
    mapper_version        text not null check (mapper_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    created_at            timestamptz not null default now(),
    constraint skill_mappings_decision_skill_key unique (decision_id, skill_id),
    constraint skill_mappings_decision_fk foreign key (decision_id, learner_id)
        references public.mapping_decisions (id, learner_id) on delete cascade,
    constraint skill_mappings_segment_fk foreign key (segment_id, learner_id)
        references public.activity_segments (id, learner_id) on delete cascade
);

create index skill_mappings_learner_skill_idx on public.skill_mappings (learner_id, skill_id);

-- The mapper may select only candidate ids supplied by retrieval (§9.4, §20.2),
-- and only ACTIVE assessable registry nodes.
create function public.skill_mappings_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if not exists (
        select 1 from public.mapping_decisions d
         where d.id = new.decision_id and d.segment_id = new.segment_id
           and new.skill_id = any (d.mapper_candidate_ids)) then
        raise exception 'skill % was not a retrieval candidate for decision %', new.skill_id, new.decision_id
            using errcode = '23514';
    end if;
    if not exists (
        select 1 from public.skill_nodes n
         where n.id = new.skill_id and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')) then
        raise exception 'skill % is not an ACTIVE assessable skill', new.skill_id using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger skill_mappings_guard
    before insert on public.skill_mappings
    for each row execute function public.skill_mappings_guard();

create trigger skill_mappings_append_only
    before update on public.skill_mappings
    for each row execute function public.reject_update();

revoke execute on function public.skill_mappings_guard() from public, anon, authenticated;

-- Job outcome, e.g. MAPPED / ABSTAINED / NON_LEARNING / DEFERRED_TO_ASSISTANT.
alter table public.processing_jobs
    add column outcome text check (outcome is null or outcome ~ '^[A-Z][A-Z0-9_]{2,63}$');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.model_runs enable row level security;
alter table public.policy_config enable row level security;
alter table public.courses enable row level security;
alter table public.course_memberships enable row level security;
alter table public.skill_nodes enable row level security;
alter table public.skill_aliases enable row level security;
alter table public.skill_edges enable row level security;
alter table public.course_skills enable row level security;
alter table public.skill_embeddings enable row level security;
alter table public.activity_segments enable row level security;
alter table public.skill_candidates enable row level security;
alter table public.mapping_decisions enable row level security;
alter table public.skill_mappings enable row level security;

revoke all on table
    public.model_runs, public.policy_config, public.courses, public.course_memberships,
    public.skill_nodes, public.skill_aliases, public.skill_edges, public.course_skills,
    public.skill_embeddings, public.activity_segments, public.skill_candidates,
    public.mapping_decisions, public.skill_mappings
    from anon, authenticated;

-- Readable by signed-in clients (SELECT only; every write goes through the backend).
-- model_runs, policy_config, skill_embeddings and skill_candidates stay server-only.
grant select on table
    public.courses, public.course_memberships, public.skill_nodes, public.skill_aliases,
    public.skill_edges, public.course_skills, public.activity_segments,
    public.mapping_decisions, public.skill_mappings
    to authenticated;

create policy courses_select_member on public.courses
    for select to authenticated
    using (owner_id = (select auth.uid()) or private.is_course_member(id));

create policy course_memberships_select_own on public.course_memberships
    for select to authenticated using (user_id = (select auth.uid()));

create policy course_skills_select_member on public.course_skills
    for select to authenticated using (private.is_course_member(course_id));

-- The registry is shared, canonical, non-personal data: ACTIVE rows are readable.
create policy skill_nodes_select_active on public.skill_nodes
    for select to authenticated using (status = 'ACTIVE');

create policy skill_aliases_select_active on public.skill_aliases
    for select to authenticated
    using (exists (select 1 from public.skill_nodes n where n.id = skill_id and n.status = 'ACTIVE'));

create policy skill_edges_select_active on public.skill_edges
    for select to authenticated
    using (exists (select 1 from public.skill_nodes n where n.id = from_skill_id and n.status = 'ACTIVE')
       and exists (select 1 from public.skill_nodes n where n.id = to_skill_id and n.status = 'ACTIVE'));

create policy activity_segments_select_own on public.activity_segments
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy mapping_decisions_select_own on public.mapping_decisions
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy skill_mappings_select_own on public.skill_mappings
    for select to authenticated using ((select auth.uid()) = learner_id);
