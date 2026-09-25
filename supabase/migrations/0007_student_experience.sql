-- 0007_student_experience.sql
-- SkillMirror P5: the correction loop (feedback) and the Engine 16 action queue
-- (recommendations). Architecture §5.1 (Engine 16), §5.2 (Feedback & Correction), §7.1, §7.2,
-- §12.1, §13, §16 "Feedback"; ADR 0006.
--
-- * feedback: the learner's corrections and evaluations. DONT_COUNT and WRONG_SKILL exclude
--   derived evidence through the one-way evidence_events exclusion of migration 0005 and are
--   followed by a ledger recompute; nothing is ever deleted, so raw_messages, activity_segments,
--   mapping_decisions, skill_mappings, attributions and evidence_events stay inspectable.
--   EVALUATION stores the learner's view of SkillMirror's assessment and never changes
--   evidence. The row is append-only. It records its own effect (the evidence it excluded and
--   the skills recomputed), and a guard re-checks that effect and resolves the target's skill,
--   mapping and segment instead of trusting the client.
--   Idempotency: (user_id, client_request_id) is unique, and a learner has at most one
--   DONT_COUNT / WRONG_SKILL per target, so a retried or repeated correction never duplicates.
-- * A correction also holds for evidence written later from the same mapping or task unit (a
--   job that was deferred when the learner corrected it): such evidence is born excluded.
-- * recommendations: a rebuildable, deterministic action queue derived from the ledger (it is
--   never evidence). At most one ACTIVE recommendation per learner and skill. A change of
--   action supersedes the old row and inserts a new one; resolved rows are immutable. An
--   UNKNOWN skill never receives PRACTICE or PREREQUISITE (unknown is not weak).
--
-- Learners may SELECT their own rows. Clients never write: feedback goes through the backend
-- (POST /v1/feedback), which authorizes the target; recommendations are written only by the
-- backend's deterministic refresh.

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and the backend Pydantic models)
-- ---------------------------------------------------------------------------
create type public.feedback_action as enum ('WRONG_SKILL', 'DONT_COUNT', 'EVALUATION');
create type public.feedback_target_type as enum (
    'EVIDENCE_EVENT', 'SKILL_MAPPING', 'ACTIVITY_SEGMENT', 'SKILL', 'RECOMMENDATION');
create type public.feedback_verdict as enum ('AGREE', 'DISAGREE', 'UNCLEAR');
create type public.recommendation_type as enum ('NO_ACTION', 'PRACTICE', 'VERIFY', 'PREREQUISITE', 'REVERIFY');
create type public.recommendation_state as enum ('ACTIVE', 'SUPERSEDED', 'COMPLETED', 'DISMISSED');

-- ---------------------------------------------------------------------------
-- recommendations: the Engine 16 action queue (§5.1, §7.1)
-- ---------------------------------------------------------------------------
create table public.recommendations (
    id                uuid primary key default gen_random_uuid(),
    learner_id        uuid not null references public.profiles (id) on delete cascade,
    skill_id          uuid not null references public.skill_nodes (id),
    type              public.recommendation_type not null,
    -- 0..100, higher first. NO_ACTION is always 0.
    priority          smallint not null check (priority between 0 and 100),
    reason_code       text not null check (reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    -- PREREQUISITE: the prerequisite skill to strengthen first.
    related_skill_id  uuid references public.skill_nodes (id),
    state             public.recommendation_state not null default 'ACTIVE',
    -- The mastery state the rules read (UNKNOWN when the skill has no ledger row).
    mastery_state     public.mastery_state not null,
    -- The deterministic rule inputs, so every recommendation stays explainable.
    inputs            jsonb not null check (jsonb_typeof(inputs) = 'object'),
    algorithm_version text not null check (algorithm_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    resolved_at       timestamptz,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    constraint recommendations_related_skill check ((type = 'PREREQUISITE') = (related_skill_id is not null)),
    constraint recommendations_related_not_self check (related_skill_id is null or related_skill_id <> skill_id),
    constraint recommendations_no_action_priority check (type <> 'NO_ACTION' or priority = 0),
    constraint recommendations_resolution check ((state = 'ACTIVE') = (resolved_at is null)),
    -- Unknown is not weak: without enough evidence there is no practice or prerequisite
    -- judgement (only "no action" or, for repeated unverified delegation, a verification).
    constraint recommendations_unknown_not_weak check (
        mastery_state <> 'UNKNOWN' or type in ('NO_ACTION', 'VERIFY')),
    constraint recommendations_practice_needs_evidence check (
        type not in ('PRACTICE', 'PREREQUISITE') or mastery_state in ('EMERGING', 'DEVELOPING')),
    constraint recommendations_reverify_state check (
        type <> 'REVERIFY' or mastery_state = 'NEEDS_REVERIFICATION')
);

comment on table public.recommendations is
    'Engine 16 action queue (NO_ACTION/PRACTICE/VERIFY/PREREQUISITE/REVERIFY): a deterministic, rebuildable projection of the ledger (P5). Not evidence.';

create unique index recommendations_one_active_key
    on public.recommendations (learner_id, skill_id) where state = 'ACTIVE';
create index recommendations_learner_active_idx
    on public.recommendations (learner_id, priority desc) where state = 'ACTIVE';
create index recommendations_learner_skill_idx on public.recommendations (learner_id, skill_id, created_at desc);

-- While ACTIVE, a refresh may update only the priority, the mastery state it read and the
-- inputs, or resolve the row (SUPERSEDED / COMPLETED / DISMISSED). A resolved row is final;
-- a different action is a new row.
create function public.recommendations_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if old.state <> 'ACTIVE' then
        raise exception 'recommendation % is resolved (%) and immutable', old.id, old.state
            using errcode = '55000';
    end if;
    if (new.id, new.learner_id, new.skill_id, new.type, new.reason_code, new.related_skill_id,
        new.algorithm_version, new.created_at)
       is distinct from
       (old.id, old.learner_id, old.skill_id, old.type, old.reason_code, old.related_skill_id,
        old.algorithm_version, old.created_at) then
        raise exception 'a recommendation''s action is immutable: supersede it and insert a new one'
            using errcode = '55000';
    end if;
    new.updated_at := now();
    return new;
end;
$$;

create trigger recommendations_guard_update
    before update on public.recommendations
    for each row execute function public.recommendations_guard_update();

revoke execute on function public.recommendations_guard_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- feedback: the correction loop (§5.2, §7.1, §16)
-- ---------------------------------------------------------------------------
create table public.feedback (
    id                    uuid primary key default gen_random_uuid(),
    user_id               uuid not null references public.profiles (id) on delete cascade,
    target_type           public.feedback_target_type not null,
    target_id             uuid not null,
    action                public.feedback_action not null,
    -- EVALUATION only: the learner's view of the assessment or explanation.
    verdict               public.feedback_verdict,
    -- Learner-written text: stored and rendered as plain text only.
    note                  text check (note is null or char_length(btrim(note)) between 1 and 2000),
    -- Resolved from the target by the guard (never taken from the client).
    skill_id              uuid references public.skill_nodes (id),
    mapping_id            uuid references public.skill_mappings (id) on delete cascade,
    segment_id            uuid references public.activity_segments (id) on delete cascade,
    -- The effect of a correction: the evidence it excluded (one-way) and the skills whose
    -- ledger rows were recomputed. Always empty for EVALUATION.
    excluded_evidence_ids uuid[] not null default '{}',
    recomputed_skill_ids  uuid[] not null default '{}',
    -- Idempotency: the client's key, and a hash of the request first sent with it.
    client_request_id     text not null check (client_request_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
    request_hash          text not null check (request_hash ~ '^[0-9a-f]{64}$'),
    created_at            timestamptz not null default now(),
    constraint feedback_request_key unique (user_id, client_request_id),
    -- WRONG_SKILL is about a mapping (directly or through its evidence); DONT_COUNT may also
    -- cover a whole task unit; EVALUATION may concern anything the learner can see.
    constraint feedback_action_target check (
        (action = 'WRONG_SKILL' and target_type in ('EVIDENCE_EVENT', 'SKILL_MAPPING'))
        or (action = 'DONT_COUNT' and target_type in ('EVIDENCE_EVENT', 'SKILL_MAPPING', 'ACTIVITY_SEGMENT'))
        or action = 'EVALUATION'),
    constraint feedback_verdict_only_evaluation check (action = 'EVALUATION' or verdict is null),
    constraint feedback_evaluation_content check (action <> 'EVALUATION' or verdict is not null or note is not null),
    -- An evaluation never changes evidence: only DONT_COUNT / WRONG_SKILL exclude.
    constraint feedback_evaluation_no_effect check (
        action <> 'EVALUATION'
        or (cardinality(excluded_evidence_ids) = 0 and cardinality(recomputed_skill_ids) = 0)),
    -- A correction names its scope: a skill mapping, or (DONT_COUNT of a task unit) a segment.
    constraint feedback_correction_scope check (
        action = 'EVALUATION'
        or (segment_id is not null
            and (mapping_id is not null or (action = 'DONT_COUNT' and target_type = 'ACTIVITY_SEGMENT'))))
);

comment on table public.feedback is
    'Learner corrections (WRONG_SKILL, DONT_COUNT) and evaluations (P5). Corrections exclude derived evidence one-way; provenance is never deleted. Append-only.';

-- A retried or repeated correction of the same target is the same correction.
create unique index feedback_one_correction_key
    on public.feedback (user_id, action, target_type, target_id)
    where action in ('WRONG_SKILL', 'DONT_COUNT');
create index feedback_user_idx on public.feedback (user_id, created_at desc);
create index feedback_user_skill_idx on public.feedback (user_id, skill_id);
create index feedback_mapping_idx on public.feedback (mapping_id) where mapping_id is not null;
create index feedback_segment_idx on public.feedback (segment_id) where segment_id is not null;
create index feedback_excluded_evidence_idx on public.feedback using gin (excluded_evidence_ids);

-- The target must exist and belong to the learner. Its skill, mapping and segment are copied
-- from it, and a correction's recorded effect must be real: every id in
-- excluded_evidence_ids is this learner's excluded captured-activity evidence inside the
-- correction's scope (the mapping, else the segment).
create function public.feedback_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    t record;
begin
    new.skill_id := null;
    new.mapping_id := null;
    new.segment_id := null;
    if new.target_type = 'EVIDENCE_EVENT' then
        select e.learner_id, e.skill_id, e.mapping_id, e.segment_id, e.source_type
          into t from public.evidence_events e where e.id = new.target_id;
        if not found or t.learner_id <> new.user_id then
            raise exception 'feedback target % not found', new.target_id using errcode = '23503';
        end if;
        if new.action <> 'EVALUATION' and t.source_type <> 'AI_ACTIVITY' then
            raise exception 'only evidence from captured activity can be corrected' using errcode = '23514';
        end if;
        new.skill_id := t.skill_id;
        new.mapping_id := t.mapping_id;
        new.segment_id := t.segment_id;
    elsif new.target_type = 'SKILL_MAPPING' then
        select m.learner_id, m.skill_id, m.segment_id
          into t from public.skill_mappings m where m.id = new.target_id;
        if not found or t.learner_id <> new.user_id then
            raise exception 'feedback target % not found', new.target_id using errcode = '23503';
        end if;
        new.skill_id := t.skill_id;
        new.mapping_id := new.target_id;
        new.segment_id := t.segment_id;
    elsif new.target_type = 'ACTIVITY_SEGMENT' then
        select s.learner_id into t from public.activity_segments s where s.id = new.target_id;
        if not found or t.learner_id <> new.user_id then
            raise exception 'feedback target % not found', new.target_id using errcode = '23503';
        end if;
        new.segment_id := new.target_id;
    elsif new.target_type = 'SKILL' then
        if not exists (select 1 from public.skill_nodes n where n.id = new.target_id) then
            raise exception 'feedback target % not found', new.target_id using errcode = '23503';
        end if;
        new.skill_id := new.target_id;
    else  -- RECOMMENDATION
        select r.learner_id, r.skill_id into t from public.recommendations r where r.id = new.target_id;
        if not found or t.learner_id <> new.user_id then
            raise exception 'feedback target % not found', new.target_id using errcode = '23503';
        end if;
        new.skill_id := t.skill_id;
    end if;

    if exists (
        select 1
          from unnest(new.excluded_evidence_ids) as x(id)
          left join public.evidence_events e on e.id = x.id
         where e.id is null
            or e.learner_id <> new.user_id
            or not e.excluded
            or e.source_type <> 'AI_ACTIVITY'
            or not ((new.mapping_id is not null and e.mapping_id = new.mapping_id)
                    or (new.mapping_id is null and e.segment_id = new.segment_id))) then
        raise exception 'excluded_evidence_ids must be excluded evidence inside the correction''s scope'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger feedback_guard
    before insert on public.feedback
    for each row execute function public.feedback_guard();

create trigger feedback_append_only
    before update on public.feedback
    for each row execute function public.reject_update();

revoke execute on function public.feedback_guard() from public, anon, authenticated;

-- A correction also holds for evidence written after it (e.g. a job deferred by the model
-- quota finishes later): captured-activity evidence of a corrected mapping or task unit is
-- inserted already excluded. This is an insert-time value, not an update, so the
-- append-only guard of migration 0005 is untouched. (Fires before evidence_events_guard:
-- triggers of one event run in name order.)
create function public.evidence_events_apply_corrections()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    corrected public.feedback_action;
begin
    if new.source_type <> 'AI_ACTIVITY' or new.excluded then
        return new;
    end if;
    select f.action into corrected
      from public.feedback f
     where f.user_id = new.learner_id
       and f.action in ('WRONG_SKILL', 'DONT_COUNT')
       and ((f.mapping_id is not null and f.mapping_id = new.mapping_id)
            or (f.mapping_id is null and f.segment_id = new.segment_id))
     order by (f.action = 'WRONG_SKILL') desc, f.created_at
     limit 1;
    if found then
        new.excluded := true;
        new.exclusion_reason := case corrected when 'WRONG_SKILL' then 'LEARNER_WRONG_SKILL'
                                               else 'LEARNER_DONT_COUNT' end;
        new.excluded_at := now();
    end if;
    return new;
end;
$$;

create trigger evidence_events_apply_corrections
    before insert on public.evidence_events
    for each row execute function public.evidence_events_apply_corrections();

revoke execute on function public.evidence_events_apply_corrections() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- policy_config: Engine 16 rules and the learner-facing debt bands (Appendix B)
-- ---------------------------------------------------------------------------
insert into public.policy_config (key, value, description) values
    ('recommendations',
     '{"max_active_verify": 2, "prerequisite_gap_states": ["EMERGING"],
       "debt_bands": {"moderate_min": 15, "high_min": 25}}',
     'Engine 16 (P5): at most max_active_verify ACTIVE VERIFY recommendations (Appendix B: 2 verification recommendations per learner); a prerequisite in a gap state makes PREREQUISITE; qualitative debt bands (LOW below moderate_min, HIGH from high_min) for eligible debt only.');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.feedback enable row level security;
alter table public.recommendations enable row level security;

revoke all on table public.feedback, public.recommendations from anon, authenticated;

-- Learners read their own feedback and recommendations; every write goes through the backend.
grant select on table public.feedback, public.recommendations to authenticated;

create policy feedback_select_own on public.feedback
    for select to authenticated using ((select auth.uid()) = user_id);
create policy recommendations_select_own on public.recommendations
    for select to authenticated using ((select auth.uid()) = learner_id);
