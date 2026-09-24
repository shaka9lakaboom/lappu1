-- 0002_capture_ingestion.sql
-- SkillMirror P1: raw acquisition storage (architecture §6.5, §6.6, §7.1-§7.3).
--
-- conversations, raw_messages, attachments and the durable processing_jobs
-- queue. Raw activity is append-oriented: captured message revisions are never
-- updated in place. Only the backend (table owner / service connection) writes;
-- learners can read their own rows through RLS and nothing else.

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and app/ingestion/models.py)
-- ---------------------------------------------------------------------------
create type public.source_provider as enum ('chatgpt', 'claude', 'gemini', 'skillmirror');
create type public.source_method as enum ('browser_extension', 'native', 'manual');
create type public.message_role as enum ('user', 'assistant');
create type public.content_format as enum ('text', 'markdown');
create type public.attachment_kind as enum ('file', 'image', 'unknown');
create type public.job_state as enum ('PENDING', 'PROCESSING', 'COMPLETED', 'RETRY_WAIT', 'FAILED');

-- ---------------------------------------------------------------------------
-- conversations
-- ---------------------------------------------------------------------------
create table public.conversations (
    id              uuid primary key default gen_random_uuid(),
    learner_id      uuid not null references public.profiles (id) on delete cascade,
    source_provider public.source_provider not null,
    -- Provider conversation id, or null when the page exposed none. All of a
    -- learner's id-less messages for one provider share one container.
    external_id     text check (external_id is null or (external_id ~ '^[A-Za-z0-9._:-]+$' and char_length(external_id) <= 256)),
    first_seen_at   timestamptz not null,
    last_seen_at    timestamptz not null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    constraint conversations_seen_order check (last_seen_at >= first_seen_at),
    constraint conversations_id_learner_key unique (id, learner_id),
    constraint conversations_learner_provider_external_key
        unique nulls not distinct (learner_id, source_provider, external_id)
);

comment on table public.conversations is 'Provider conversation container per learner (architecture §7.1).';

create trigger conversations_set_updated_at
    before update on public.conversations
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- raw_messages: immutable captured message revisions
-- ---------------------------------------------------------------------------
create table public.raw_messages (
    id                         uuid primary key default gen_random_uuid(),
    learner_id                 uuid not null,
    conversation_id            uuid not null,
    source_provider            public.source_provider not null,
    source_method              public.source_method not null,
    external_message_id        text check (external_message_id is null or (external_message_id ~ '^[A-Za-z0-9._:-]+$' and char_length(external_message_id) <= 256)),
    external_parent_message_id text check (external_parent_message_id is null or (external_parent_message_id ~ '^[A-Za-z0-9._:-]+$' and char_length(external_parent_message_id) <= 256)),
    message_index              integer check (message_index is null or message_index between 0 and 100000),
    role                       public.message_role not null,
    content_text               text not null check (char_length(content_text) between 1 and 100000),
    content_format             public.content_format not null,
    -- SHA-256 hex of the normalized content, computed by the backend.
    content_hash               text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    -- §6.6 fallback fingerprint, computed by the backend for every message.
    fingerprint                text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
    revision_index             integer not null check (revision_index between 0 and 1000000),
    occurred_at                timestamptz,
    captured_at                timestamptz not null,
    received_at                timestamptz not null default now(),
    provider_model             text check (provider_model is null or provider_model ~ '^[A-Za-z0-9._:/-]{1,100}$'),
    context_incomplete         boolean not null,
    -- FK to courses is added with the courses table (P2).
    active_course_id           uuid,
    -- Client provenance: the envelope's event_id and dedup key. Never trusted for identity.
    client_event_uuid          uuid not null,
    client_event_id            text not null check (char_length(client_event_id) between 1 and 300),
    -- Server-assembled capture context (extension/adapter version, client revision claim).
    capture_metadata           jsonb not null default '{}'::jsonb check (jsonb_typeof(capture_metadata) = 'object'),
    created_at                 timestamptz not null default now(),
    constraint raw_messages_id_learner_key unique (id, learner_id),
    -- A message always belongs to a conversation of the same learner.
    constraint raw_messages_conversation_fk foreign key (conversation_id, learner_id)
        references public.conversations (id, learner_id) on delete cascade
);

comment on table public.raw_messages is
    'Immutable captured message revisions (architecture §7.1, §7.2). Never updated in place.';

-- §6.6 preferred identity: (learner, provider, external message id, revision).
create unique index raw_messages_preferred_identity_key
    on public.raw_messages (learner_id, source_provider, external_message_id, revision_index)
    where external_message_id is not null;

-- The same content under the same message id is the same revision, whatever
-- revision number a client claims.
create unique index raw_messages_message_content_key
    on public.raw_messages (learner_id, source_provider, external_message_id, content_hash)
    where external_message_id is not null;

-- §6.6 fallback identity when the provider exposes no message id.
create unique index raw_messages_fallback_identity_key
    on public.raw_messages (learner_id, fingerprint)
    where external_message_id is null;

create index raw_messages_learner_received_idx on public.raw_messages (learner_id, received_at desc);
create index raw_messages_conversation_order_idx on public.raw_messages (conversation_id, message_index, captured_at);

create function public.reject_raw_message_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'raw_messages is append-only; insert a new revision instead'
        using errcode = '55000';
end;
$$;

create trigger raw_messages_append_only
    before update on public.raw_messages
    for each row execute function public.reject_raw_message_update();

-- ---------------------------------------------------------------------------
-- attachments: metadata only in V1
-- ---------------------------------------------------------------------------
create table public.attachments (
    id                uuid primary key default gen_random_uuid(),
    raw_message_id    uuid not null,
    learner_id        uuid not null,
    position          integer not null check (position between 0 and 19),
    kind              public.attachment_kind not null,
    filename          text check (filename is null or char_length(filename) between 1 and 255),
    mime_type         text check (mime_type is null or mime_type ~ '^[a-z0-9.+-]{1,63}/[a-z0-9.+-]{1,63}$'),
    content_available boolean not null default false,
    -- Always null in V1: attachment contents are not ingested.
    storage_path      text,
    metadata          jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_at        timestamptz not null default now(),
    constraint attachments_message_fk foreign key (raw_message_id, learner_id)
        references public.raw_messages (id, learner_id) on delete cascade,
    constraint attachments_message_position_key unique (raw_message_id, position)
);

comment on table public.attachments is 'Attachment metadata seen on the provider page. Contents are not captured in V1.';

create index attachments_learner_idx on public.attachments (learner_id);

-- ---------------------------------------------------------------------------
-- processing_jobs: durable PostgreSQL work queue (architecture §7.3)
-- ---------------------------------------------------------------------------
-- Workers claim with FOR UPDATE SKIP LOCKED (services/backend/app/jobs/queue.py).
-- PENDING -> PROCESSING -> COMPLETED; failure -> RETRY_WAIT -> (claimable again);
-- attempts exhausted -> FAILED.
create table public.processing_jobs (
    id           uuid primary key default gen_random_uuid(),
    job_type     text not null check (job_type ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    entity_type  text not null check (entity_type ~ '^[a-z][a-z0-9_]{2,63}$'),
    entity_id    uuid not null,
    learner_id   uuid references public.profiles (id) on delete cascade,
    state        public.job_state not null default 'PENDING',
    attempts     integer not null default 0 check (attempts >= 0),
    max_attempts integer not null default 3 check (max_attempts between 1 and 20),
    available_at timestamptz not null default now(),
    locked_at    timestamptz,
    locked_by    text,
    last_error   text check (last_error is null or char_length(last_error) <= 2000),
    completed_at timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    -- One job of a type per entity: job creation is idempotent.
    constraint processing_jobs_type_entity_key unique (job_type, entity_id),
    constraint processing_jobs_lock_consistency check (
        (state = 'PROCESSING') = (locked_at is not null)
    )
);

comment on table public.processing_jobs is 'Durable worker queue (architecture §7.3). No Redis/Celery.';

create index processing_jobs_claimable_idx
    on public.processing_jobs (available_at, created_at)
    where state in ('PENDING', 'RETRY_WAIT');
create index processing_jobs_learner_idx on public.processing_jobs (learner_id, created_at desc);

create trigger processing_jobs_set_updated_at
    before update on public.processing_jobs
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
-- Clients get read access to their own rows only. All writes go through the
-- backend ingestion API, which derives the learner from the verified JWT.
alter table public.conversations enable row level security;
alter table public.raw_messages enable row level security;
alter table public.attachments enable row level security;
alter table public.processing_jobs enable row level security;

revoke all on table public.conversations, public.raw_messages, public.attachments, public.processing_jobs
    from anon, authenticated;
grant select on table public.conversations, public.raw_messages, public.attachments, public.processing_jobs
    to authenticated;

create policy conversations_select_own on public.conversations
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy raw_messages_select_own on public.raw_messages
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy attachments_select_own on public.attachments
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy processing_jobs_select_own on public.processing_jobs
    for select to authenticated using ((select auth.uid()) = learner_id);

revoke execute on function public.reject_raw_message_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- activity_feed: the learner's raw activity + processing status (Activity page)
-- ---------------------------------------------------------------------------
-- security_invoker: the caller's RLS on the base tables applies, so a learner
-- sees only their own rows. Exposes a short preview, not the full content.
create view public.activity_feed
with (security_invoker = true)
as
select
    m.id,
    m.learner_id,
    m.conversation_id,
    c.external_id                  as external_conversation_id,
    m.source_provider,
    m.role,
    m.message_index,
    m.revision_index,
    m.captured_at,
    m.received_at,
    m.context_incomplete,
    left(m.content_text, 280)      as preview,
    char_length(m.content_text)    as content_chars,
    j.state                        as processing_state,
    j.attempts                     as processing_attempts
from public.raw_messages m
join public.conversations c on c.id = m.conversation_id
left join public.processing_jobs j
       on j.entity_id = m.id and j.job_type = 'PROCESS_RAW_MESSAGE';

comment on view public.activity_feed is 'Raw capture status per learner (P1 Activity page). RLS via security_invoker.';

revoke all on public.activity_feed from anon, authenticated;
grant select on public.activity_feed to authenticated;
