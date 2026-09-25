-- 0005_attribution_evidence.sql
-- SkillMirror P3B: contribution attribution and immutable EvidenceEvents
-- (architecture §2.2, §7.1, §7.2, §9.5, §9.6, §10.1, Appendix A.3, Appendix B; ADR 0005).
--
-- Provenance chain (every link is a foreign key or a guarded copy):
--   raw_messages -> activity_segments -> mapping_decisions -> skill_mappings (ACCEPTED)
--     -> attributions -> evidence_events -> model_runs
--
-- * attributions: one row per ACCEPTED skill mapping and attribution version. It holds the
--   model's categorical actor judgement (Appendix A.3), or an explicit abstention when the
--   structured output stayed invalid after one repair. evidence_decision records what the
--   deterministic evidence qualification made of it.
-- * evidence_events: immutable evidence, at most one per attribution. Only qualified
--   evidence is written: an UNKNOWN actor, a low-confidence attribution, an undetermined
--   outcome or an ungrounded learner span abstain (no row). EXPOSURE and OBSERVATION carry
--   strength 0 and no outcome, enforced here as well as in the backend.
-- * Only captured AI activity produces evidence in P3B. VERIFICATION / ASSESSMENT /
--   TEACHER sources arrive with their own migrations (P6/P7).
--
-- Only the backend writes. Learners may SELECT their own rows; clients can never
-- create or modify evidence. Mastery, the ledger and debt are P4 (migration 0006).

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and the backend Pydantic models)
-- ---------------------------------------------------------------------------
create type public.evidence_actor as enum ('STUDENT', 'AI', 'SHARED', 'UNKNOWN');
create type public.evidence_type as enum (
    'EXPOSURE', 'OBSERVATION', 'ASSISTED_ATTEMPT', 'INDEPENDENT_EXPLANATION',
    'INDEPENDENT_APPLICATION', 'TRANSFER', 'VERIFICATION', 'EXECUTION_RESULT', 'TEACHER_EVIDENCE');
create type public.outcome_signal as enum ('CORRECT', 'INCORRECT', 'PARTIAL', 'NOT_APPLICABLE');
create type public.evidence_source_type as enum ('AI_ACTIVITY', 'VERIFICATION', 'ASSESSMENT', 'TEACHER');
create type public.attribution_status as enum ('ATTRIBUTED', 'ABSTAINED');

-- ---------------------------------------------------------------------------
-- attributions: contribution attribution per accepted skill mapping (§9.5, Appendix A.3)
-- ---------------------------------------------------------------------------
create table public.attributions (
    id                     uuid primary key default gen_random_uuid(),
    learner_id             uuid not null references public.profiles (id) on delete cascade,
    mapping_id             uuid not null references public.skill_mappings (id) on delete cascade,
    decision_id            uuid not null references public.mapping_decisions (id) on delete cascade,
    segment_id             uuid not null references public.activity_segments (id) on delete cascade,
    skill_id               uuid not null references public.skill_nodes (id),
    status                 public.attribution_status not null,
    -- The model's judgement (Appendix A.3). All null when the attribution ABSTAINED.
    actor                  public.evidence_actor,
    confidence             real check (confidence is null or confidence between 0 and 1),
    student_span           text check (student_span is null or char_length(student_span) <= 2000),
    ai_span                text check (ai_span is null or char_length(ai_span) <= 2000),
    -- Appendix A.3 evidence_type as proposed by the model (OTHER = no evidence).
    proposed_evidence_type text check (proposed_evidence_type is null or proposed_evidence_type in (
                               'EXPOSURE', 'OBSERVATION', 'ASSISTED_ATTEMPT', 'INDEPENDENT_EXPLANATION',
                               'INDEPENDENT_APPLICATION', 'TRANSFER', 'OTHER')),
    outcome_signal         public.outcome_signal,
    rationale_code         text not null check (rationale_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    -- Deterministic evidence qualification outcome: EVIDENCE_CREATED or the abstention reason.
    evidence_decision      text not null check (evidence_decision ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    -- The SKILL_ATTRIBUTION run (on abstention: the last, invalid, run).
    model_run_id           uuid references public.model_runs (id),
    prompt_version         text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    attributor_version     text not null check (attributor_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    attribution_version    text not null check (attribution_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    processing_job_id      uuid,
    created_at             timestamptz not null default now(),
    -- Replay-safe: one attribution per accepted mapping and attribution version.
    constraint attributions_mapping_version_key unique (mapping_id, attribution_version),
    constraint attributions_judgement_shape check (
        (status = 'ATTRIBUTED' and actor is not null and confidence is not null
            and proposed_evidence_type is not null and outcome_signal is not null)
        or (status = 'ABSTAINED' and actor is null and confidence is null and student_span is null
            and ai_span is null and proposed_evidence_type is null and outcome_signal is null
            and evidence_decision <> 'EVIDENCE_CREATED'))
);

comment on table public.attributions is
    'Contribution attribution (STUDENT/AI/SHARED/UNKNOWN) per ACCEPTED skill mapping, or an explicit abstention (P3B). Append-only.';

create index attributions_learner_idx on public.attributions (learner_id, created_at desc);
create index attributions_segment_idx on public.attributions (segment_id);

-- Attribution refers only to an existing ACCEPTED skill mapping, and copies its
-- decision / segment / learner / skill exactly.
create function public.attributions_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    m record;
begin
    select sm.decision_id, sm.segment_id, sm.learner_id, sm.skill_id, sm.status
      into m
      from public.skill_mappings sm
     where sm.id = new.mapping_id;
    if not found then
        raise exception 'skill mapping % does not exist', new.mapping_id using errcode = '23503';
    end if;
    if m.status <> 'ACCEPTED' then
        raise exception 'attribution requires an ACCEPTED skill mapping (mapping % is %)', new.mapping_id, m.status
            using errcode = '23514';
    end if;
    if (m.decision_id, m.segment_id, m.learner_id, m.skill_id)
       is distinct from (new.decision_id, new.segment_id, new.learner_id, new.skill_id) then
        raise exception 'attribution provenance does not match skill mapping %', new.mapping_id
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger attributions_guard
    before insert on public.attributions
    for each row execute function public.attributions_guard();

create trigger attributions_append_only
    before update on public.attributions
    for each row execute function public.reject_update();

revoke execute on function public.attributions_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- evidence_events: immutable evidence (§7.2, §9.6, §10.1)
-- ---------------------------------------------------------------------------
create table public.evidence_events (
    id                     uuid primary key default gen_random_uuid(),
    learner_id             uuid not null references public.profiles (id) on delete cascade,
    skill_id               uuid not null references public.skill_nodes (id),
    source_type            public.evidence_source_type not null,
    -- For AI_ACTIVITY: the attribution id.
    source_id              uuid not null,
    -- Provenance to captured activity (AI_ACTIVITY). One evidence event per attribution.
    attribution_id         uuid unique references public.attributions (id) on delete cascade,
    mapping_id             uuid references public.skill_mappings (id) on delete cascade,
    decision_id            uuid references public.mapping_decisions (id) on delete cascade,
    segment_id             uuid references public.activity_segments (id) on delete cascade,
    raw_message_ids        uuid[] not null default '{}',
    evidence_type          public.evidence_type not null,
    -- Effective actor after the deterministic guards (e.g. copied AI text -> AI).
    actor                  public.evidence_actor not null,
    outcome_signal         public.outcome_signal not null,
    -- 0..1 only where performance exists (CORRECT 1, PARTIAL policy value, INCORRECT 0).
    outcome                double precision check (outcome is null or outcome between 0 and 1),
    difficulty             double precision not null check (difficulty between 0 and 1),
    difficulty_multiplier  double precision not null check (difficulty_multiplier > 0),
    independence           double precision not null check (independence between 0 and 1),
    base_weight            double precision not null check (base_weight >= 0),
    -- base_weight * difficulty_multiplier * independence * evidence_confidence (Appendix B).
    strength               double precision not null check (strength >= 0),
    mapping_confidence     real not null check (mapping_confidence between 0 and 1),
    attribution_confidence real not null check (attribution_confidence between 0 and 1),
    evidence_confidence    real not null check (evidence_confidence between 0 and 1),
    -- {"student": ..., "ai": ..., "mapping": ...}: the exact supporting spans.
    evidence_span          jsonb not null check (jsonb_typeof(evidence_span) = 'object'),
    -- Every model call behind this evidence (turn analysis, query, adjudication, attribution).
    model_run_ids          uuid[] not null default '{}',
    qualification_reason   text not null check (qualification_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    qualifier_version      text not null check (qualifier_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    -- The evidence/attribution policy used, so the strength stays reproducible.
    policy_snapshot        jsonb not null check (jsonb_typeof(policy_snapshot) = 'object'),
    -- When the learner activity happened (the captured turn), used for recency.
    occurred_at            timestamptz not null,
    -- User correction ("don't count this", P7) is a one-way exclusion; provenance is kept.
    excluded               boolean not null default false,
    exclusion_reason       text check (exclusion_reason is null or exclusion_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    excluded_at            timestamptz,
    processing_job_id      uuid,
    created_at             timestamptz not null default now(),
    -- P3B writes evidence from captured AI activity only (P6/P7 relax this with their sources).
    constraint evidence_events_source_p3b check (source_type = 'AI_ACTIVITY'),
    constraint evidence_events_ai_activity_shape check (
        source_type <> 'AI_ACTIVITY'
        or (attribution_id is not null and source_id = attribution_id and mapping_id is not null
            and decision_id is not null and segment_id is not null
            and cardinality(raw_message_ids) between 1 and 2
            and evidence_type in ('EXPOSURE', 'OBSERVATION', 'ASSISTED_ATTEMPT',
                                  'INDEPENDENT_EXPLANATION', 'INDEPENDENT_APPLICATION', 'TRANSFER'))),
    -- Uncertain attribution never creates evidence (§2.2 "uncertainty causes abstention").
    constraint evidence_events_actor_known check (actor <> 'UNKNOWN'),
    -- Exposure is not mastery: EXPOSURE / OBSERVATION never carry strength or an outcome.
    constraint evidence_events_exposure_guard check (
        evidence_type not in ('EXPOSURE', 'OBSERVATION')
        or (strength = 0 and outcome is null and outcome_signal = 'NOT_APPLICABLE')),
    -- A skill the AI performed is never student performance evidence.
    constraint evidence_events_ai_actor_guard check (
        actor <> 'AI' or evidence_type in ('EXPOSURE', 'OBSERVATION')),
    -- Performance evidence always has a determined outcome.
    constraint evidence_events_performance_outcome check (
        evidence_type in ('EXPOSURE', 'OBSERVATION') or outcome is not null),
    constraint evidence_events_outcome_signal check (
        (outcome_signal = 'NOT_APPLICABLE' and outcome is null)
        or (outcome_signal = 'CORRECT' and outcome = 1)
        or (outcome_signal = 'INCORRECT' and outcome = 0)
        or (outcome_signal = 'PARTIAL' and outcome > 0 and outcome < 1)),
    constraint evidence_events_confidence check (
        evidence_confidence = least(mapping_confidence, attribution_confidence)),
    constraint evidence_events_exclusion_shape check (
        (excluded and exclusion_reason is not null and excluded_at is not null)
        or (not excluded and exclusion_reason is null and excluded_at is null))
);

comment on table public.evidence_events is
    'Immutable EvidenceEvents: the source of truth for mastery and debt (architecture §7.2, §10.1). Append-only.';

create index evidence_events_learner_skill_idx on public.evidence_events (learner_id, skill_id, occurred_at desc);
create index evidence_events_segment_idx on public.evidence_events (segment_id);

-- Evidence from captured AI activity must match its attribution, mapping and segment exactly.
create function public.evidence_events_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    a record;
    sources uuid[];
    mapping_confidence real;
begin
    if new.source_type <> 'AI_ACTIVITY' then
        return new;
    end if;
    select at.learner_id, at.skill_id, at.mapping_id, at.decision_id, at.segment_id, at.status,
           at.actor, at.confidence
      into a
      from public.attributions at
     where at.id = new.attribution_id;
    if not found then
        raise exception 'attribution % does not exist', new.attribution_id using errcode = '23503';
    end if;
    if a.status <> 'ATTRIBUTED' or a.actor = 'UNKNOWN' then
        raise exception 'attribution % abstained or is UNKNOWN: it cannot create evidence', new.attribution_id
            using errcode = '23514';
    end if;
    if (a.learner_id, a.skill_id, a.mapping_id, a.decision_id, a.segment_id)
       is distinct from (new.learner_id, new.skill_id, new.mapping_id, new.decision_id, new.segment_id) then
        raise exception 'evidence provenance does not match attribution %', new.attribution_id
            using errcode = '23514';
    end if;
    if new.attribution_confidence is distinct from a.confidence then
        raise exception 'attribution_confidence must be the attribution''s confidence' using errcode = '23514';
    end if;
    select sm.confidence into mapping_confidence from public.skill_mappings sm where sm.id = new.mapping_id;
    if new.mapping_confidence is distinct from mapping_confidence then
        raise exception 'mapping_confidence must be the skill mapping''s confidence' using errcode = '23514';
    end if;
    select s.source_message_ids into sources from public.activity_segments s where s.id = new.segment_id;
    if new.raw_message_ids is distinct from sources then
        raise exception 'raw_message_ids must be the segment''s source messages' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger evidence_events_guard
    before insert on public.evidence_events
    for each row execute function public.evidence_events_guard();

-- Append-only. The only permitted update is the one-way exclusion (P7 feedback):
-- excluded false -> true with a reason and a time, nothing else changing.
create function public.evidence_events_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if not old.excluded and new.excluded
       and (to_jsonb(new) - 'excluded' - 'exclusion_reason' - 'excluded_at')
           = (to_jsonb(old) - 'excluded' - 'exclusion_reason' - 'excluded_at') then
        return new;
    end if;
    raise exception 'evidence_events is append-only (only a one-way exclusion is allowed)'
        using errcode = '55000';
end;
$$;

create trigger evidence_events_append_only
    before update on public.evidence_events
    for each row execute function public.evidence_events_guard_update();

revoke execute on function public.evidence_events_guard() from public, anon, authenticated;
revoke execute on function public.evidence_events_guard_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- policy_config: attribution gate and evidence qualification (§9.5, §9.6, Appendix B)
-- ---------------------------------------------------------------------------
insert into public.policy_config (key, value, description) values
    ('attribution',
     '{"min_confidence": 0.80, "copy_guard_min_chars": 24}',
     'Attribution gate (§9.5, B.2): below min_confidence the attribution abstains (no evidence). A learner span of at least copy_guard_min_chars found in earlier assistant output is copied AI text.'),
    ('evidence',
     '{"base_weights": {"EXPOSURE": 0.0, "OBSERVATION": 0.0, "ASSISTED_ATTEMPT": 0.35,
                        "INDEPENDENT_EXPLANATION": 0.75, "INDEPENDENT_APPLICATION": 1.0,
                        "TRANSFER": 1.25, "VERIFICATION": 1.5, "EXECUTION_RESULT": 1.25,
                        "TEACHER_EVIDENCE": 1.0},
       "independence": {"EXPOSURE": 0.0, "OBSERVATION": 0.0, "ASSISTED_ATTEMPT": 0.3,
                        "INDEPENDENT_EXPLANATION": 0.8, "INDEPENDENT_APPLICATION": 1.0,
                        "TRANSFER": 1.0, "VERIFICATION": 1.0, "EXECUTION_RESULT": 1.0,
                        "TEACHER_EVIDENCE": 1.0},
       "outcome_values": {"CORRECT": 1.0, "PARTIAL": 0.5, "INCORRECT": 0.0},
       "difficulty": {"default": 0.5, "multiplier_base": 0.75, "multiplier_slope": 0.5}}',
     'Evidence qualification (§9.6, Appendix B, B.1): strength = base_weight * (0.75 + 0.5 * difficulty) * independence * min(mapping, attribution confidence). EXPOSURE/OBSERVATION are always 0.');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.attributions enable row level security;
alter table public.evidence_events enable row level security;

revoke all on table public.attributions, public.evidence_events from anon, authenticated;

-- Learners read their own attribution and evidence; every write goes through the backend.
grant select on table public.attributions, public.evidence_events to authenticated;

create policy attributions_select_own on public.attributions
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy evidence_events_select_own on public.evidence_events
    for select to authenticated using ((select auth.uid()) = learner_id);
