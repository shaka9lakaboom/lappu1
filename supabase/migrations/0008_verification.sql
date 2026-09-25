-- 0008_verification.sql
-- SkillMirror P6: the verification loop (architecture §7.1, §10.3, §10.4, §11, §13,
-- §16 "Verification", §20.2, Appendix A.4, A.5, B; ADR 0007).
--
--   P5 VERIFY / REVERIFY recommendation -> deterministic planner -> PLANNED session
--     -> generated + validated challenge (verification_items) -> READY
--     -> learner starts (IN_PROGRESS) -> submits (SUBMITTED: the response is stored on the
--        session BEFORE any grading runs) -> grader -> immutable verification_results
--     -> ONE VERIFICATION EvidenceEvent (source_id = the result) -> ledger recompute -> EVALUATED
--
-- * A verification result never writes the ledger. It becomes evidence, and the ledger is
--   re-derived from evidence (§7.2, §20.2 release blocker). The evidence -> result link is
--   evidence_events.source_type = 'VERIFICATION', source_id = verification_results.id; the
--   result row is never updated afterwards.
-- * Incomplete verification is not failure: an ABANDONED session has no result and no
--   evidence, and a network interruption leaves IN_PROGRESS resumable.
-- * verification_items holds the answer key and rubric. It is server-only: clients have no
--   privilege on it at all, and the API returns a sanitized challenge.
-- * The P4-era check that refused VERIFIED / NEEDS_REVERIFICATION (0006) is replaced by a
--   guard that requires a passed SkillMirror verification of the skill. Migrations 0001-0007
--   are not modified.
--
-- Learners may SELECT their own sessions and results. Every write goes through the backend.

-- ---------------------------------------------------------------------------
-- Enums (mirrored in packages/contracts and the backend Pydantic models)
-- ---------------------------------------------------------------------------
create type public.verification_state as enum (
    'PLANNED', 'READY', 'IN_PROGRESS', 'SUBMITTED', 'EVALUATED', 'ABANDONED');
-- Appendix A.4 assessment types. code / sql stay representable, but no grader exists for them
-- in V1 (no sandbox), so no item of those types can be issued (verification_items_grader).
create type public.verification_assessment_type as enum (
    'mcq', 'numeric', 'code', 'sql', 'short_response', 'reasoning');
create type public.verification_grader_type as enum ('MCQ_EXACT', 'NUMERIC_TOLERANCE', 'RUBRIC_AI');
create type public.verification_evaluator_type as enum ('DETERMINISTIC', 'AI_RUBRIC');

-- ---------------------------------------------------------------------------
-- verification_sessions: the lifecycle (§11.4) and the durable learner submission
-- ---------------------------------------------------------------------------
create table public.verification_sessions (
    id                         uuid primary key default gen_random_uuid(),
    learner_id                 uuid not null references public.profiles (id) on delete cascade,
    -- The course the skill is verified for (the learner's course with the highest importance).
    course_id                  uuid references public.courses (id) on delete set null,
    skill_id                   uuid not null references public.skill_nodes (id),
    -- The P5 recommendation the planner acted on.
    recommendation_id          uuid references public.recommendations (id) on delete set null,
    trigger_type               public.recommendation_type not null
                               check (trigger_type in ('VERIFY', 'REVERIFY')),
    reason_code                text not null check (reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    state                      public.verification_state not null default 'PLANNED',
    -- The planned difficulty and its band (Appendix B.1: the evidence difficulty multiplier is
    -- only valid inside the planned band; the validator keeps the challenge inside it).
    planned_difficulty         double precision not null,
    difficulty_min             double precision not null,
    difficulty_max             double precision not null,
    -- The learner's local day of planning (profiles.timezone, else UTC): the daily budget.
    plan_day                   date not null,
    plan_timezone              text not null check (char_length(plan_timezone) between 1 and 64),
    planner_version            text not null check (planner_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    -- The deterministic planner inputs (recommendation priority, importance, policy values).
    planning_inputs            jsonb not null check (jsonb_typeof(planning_inputs) = 'object'),
    -- Challenge generation bookkeeping: attempts made and why each rejected one was rejected
    -- (codes only), so a replayed job rebuilds the exact regeneration input.
    generation_attempts        smallint not null default 0 check (generation_attempts between 0 and 10),
    generation_rejections      jsonb not null default '[]'::jsonb
                               check (jsonb_typeof(generation_rejections) = 'array'),
    -- A terminal pipeline failure, recorded honestly: no challenge could be issued (PLANNED),
    -- or the answer could not be graded with confidence (SUBMITTED). Never a learner result.
    failure_code               text check (failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    failed_at                  timestamptz,
    abandon_reason             text check (abandon_reason is null or abandon_reason ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    -- The learner's answer, stored durably when it is submitted and BEFORE grading runs
    -- (untrusted data: rendered as plain text, framed as data for any model).
    submitted_response         jsonb check (submitted_response is null or jsonb_typeof(submitted_response) = 'object'),
    submission_idempotency_key text check (submission_idempotency_key is null
                                           or submission_idempotency_key ~ '^[A-Za-z0-9._:-]{1,128}$'),
    submission_request_hash    text check (submission_request_hash is null or submission_request_hash ~ '^[0-9a-f]{64}$'),
    ready_at                   timestamptz,
    started_at                 timestamptz,
    submitted_at               timestamptz,
    evaluated_at               timestamptz,
    abandoned_at               timestamptz,
    created_at                 timestamptz not null default now(),
    updated_at                 timestamptz not null default now(),
    constraint verification_sessions_difficulty check (
        0 <= difficulty_min and difficulty_min <= planned_difficulty
        and planned_difficulty <= difficulty_max and difficulty_max <= 1),
    -- Each lifecycle timestamp exists exactly in the states that passed through it.
    constraint verification_sessions_lifecycle check (
        (ready_at is not null) = (state <> 'PLANNED')
        and (started_at is not null) = (state in ('IN_PROGRESS', 'SUBMITTED', 'EVALUATED', 'ABANDONED'))
        and (submitted_at is not null) = (state in ('SUBMITTED', 'EVALUATED'))
        and (evaluated_at is not null) = (state = 'EVALUATED')
        and (abandoned_at is not null) = (state = 'ABANDONED')
        and (abandon_reason is not null) = (state = 'ABANDONED')),
    -- A submitted session always holds the response, its idempotency key and request hash.
    constraint verification_sessions_submission check (
        (submitted_response is not null) = (state in ('SUBMITTED', 'EVALUATED'))
        and (submission_idempotency_key is not null) = (submitted_response is not null)
        and (submission_request_hash is not null) = (submitted_response is not null)),
    constraint verification_sessions_failure check (
        (failure_code is null) = (failed_at is null)
        and (failure_code is null or state in ('PLANNED', 'SUBMITTED')))
);

comment on table public.verification_sessions is
    'Verification lifecycle PLANNED -> READY -> IN_PROGRESS -> SUBMITTED -> EVALUATED (IN_PROGRESS -> ABANDONED), with the durable learner submission (P6, architecture §11.4).';

-- At most one active verification per learner and skill. A session whose pipeline failed
-- terminally is closed, so the skill can be planned again (after the planner's retry delay).
create unique index verification_sessions_one_active_key
    on public.verification_sessions (learner_id, skill_id)
    where state in ('PLANNED', 'READY', 'IN_PROGRESS', 'SUBMITTED') and failure_code is null;
-- An Idempotency-Key is used for one submission of the learner.
create unique index verification_sessions_submission_key
    on public.verification_sessions (learner_id, submission_idempotency_key)
    where submission_idempotency_key is not null;
create index verification_sessions_learner_idx on public.verification_sessions (learner_id, created_at desc);
create index verification_sessions_plan_day_idx on public.verification_sessions (learner_id, plan_day);
create index verification_sessions_skill_idx on public.verification_sessions (learner_id, skill_id, created_at desc);

-- A session is always planned first, for an assessable skill; its recommendation and course,
-- when named, are the learner's own and concern that skill.
create function public.verification_sessions_guard_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.state <> 'PLANNED' or new.generation_attempts <> 0 or new.failure_code is not null
       or new.generation_rejections <> '[]'::jsonb then
        raise exception 'a verification session starts PLANNED, without attempts or failure'
            using errcode = '23514';
    end if;
    if not exists (select 1 from public.skill_nodes n
                    where n.id = new.skill_id and n.node_kind in ('SKILL', 'SUBSKILL')) then
        raise exception 'skill % is not an assessable skill', new.skill_id using errcode = '23514';
    end if;
    if new.recommendation_id is not null and not exists (
        select 1 from public.recommendations r
         where r.id = new.recommendation_id and r.learner_id = new.learner_id
           and r.skill_id = new.skill_id and r.type = new.trigger_type) then
        raise exception 'recommendation % is not this learner''s % recommendation of the skill',
            new.recommendation_id, new.trigger_type using errcode = '23514';
    end if;
    if new.course_id is not null and not exists (
        select 1 from public.course_memberships m
          join public.course_skills cs on cs.course_id = m.course_id and cs.skill_id = new.skill_id
         where m.course_id = new.course_id and m.user_id = new.learner_id) then
        raise exception 'course % is not a course of this learner containing the skill', new.course_id
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger verification_sessions_guard_insert
    before insert on public.verification_sessions
    for each row execute function public.verification_sessions_guard_insert();

-- Legal transitions only (§11.4):
--     PLANNED -> READY -> IN_PROGRESS -> SUBMITTED -> EVALUATED;  IN_PROGRESS -> ABANDONED
-- Each transition may change only its own fields. The plan is immutable; EVALUATED and
-- ABANDONED are final. Same-state updates: generation bookkeeping or a terminal failure while
-- PLANNED, a terminal grading failure while SUBMITTED. READY needs its challenge item and
-- EVALUATED its result. (ON DELETE SET NULL of the course / recommendation is allowed.)
create function public.verification_sessions_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    changed text[];
    allowed text[];
begin
    select coalesce(array_agg(n.key order by n.key), '{}')
      into changed
      from jsonb_each(to_jsonb(new)) n
      join jsonb_each(to_jsonb(old)) o using (key)
     where n.value is distinct from o.value and n.key <> 'updated_at';
    if cardinality(changed) = 0 then
        return new;
    end if;
    if changed <@ array['course_id', 'recommendation_id']
       and (new.course_id is null or new.course_id = old.course_id)
       and (new.recommendation_id is null or new.recommendation_id = old.recommendation_id) then
        return new;
    end if;
    if old.state in ('EVALUATED', 'ABANDONED') then
        raise exception 'verification session % is % and final', old.id, old.state using errcode = '55000';
    end if;

    if new.state = old.state then
        if old.state = 'PLANNED' and old.failure_code is null then
            allowed := array['generation_attempts', 'generation_rejections', 'failure_code', 'failed_at'];
        elsif old.state = 'SUBMITTED' and old.failure_code is null then
            allowed := array['failure_code', 'failed_at'];
        else
            allowed := '{}';
        end if;
        if new.generation_attempts < old.generation_attempts then
            raise exception 'generation attempts never decrease' using errcode = '55000';
        end if;
    elsif old.state = 'PLANNED' and new.state = 'READY' then
        if old.failure_code is not null then
            raise exception 'a failed plan cannot become READY' using errcode = '55000';
        end if;
        if not exists (select 1 from public.verification_items i where i.session_id = new.id) then
            raise exception 'a READY verification needs its validated challenge item' using errcode = '55000';
        end if;
        allowed := array['state', 'ready_at', 'generation_attempts', 'generation_rejections'];
    elsif old.state = 'READY' and new.state = 'IN_PROGRESS' then
        allowed := array['state', 'started_at'];
    elsif old.state = 'IN_PROGRESS' and new.state = 'SUBMITTED' then
        allowed := array['state', 'submitted_at', 'submitted_response', 'submission_idempotency_key',
                         'submission_request_hash'];
    elsif old.state = 'SUBMITTED' and new.state = 'EVALUATED' then
        if old.failure_code is not null then
            raise exception 'a session whose grading failed is not EVALUATED' using errcode = '55000';
        end if;
        if not exists (select 1 from public.verification_results r where r.session_id = new.id) then
            raise exception 'an EVALUATED verification needs its graded result' using errcode = '55000';
        end if;
        allowed := array['state', 'evaluated_at'];
    elsif old.state = 'IN_PROGRESS' and new.state = 'ABANDONED' then
        allowed := array['state', 'abandoned_at', 'abandon_reason'];
    else
        raise exception 'illegal verification transition % -> %', old.state, new.state using errcode = '55000';
    end if;

    if not changed <@ allowed then
        raise exception 'verification session %: % may not change (%)', old.id,
            array(select unnest(changed) except select unnest(allowed)), old.state || ' -> ' || new.state
            using errcode = '55000';
    end if;
    new.updated_at := now();
    return new;
end;
$$;

create trigger verification_sessions_guard_update
    before update on public.verification_sessions
    for each row execute function public.verification_sessions_guard_update();

revoke execute on function public.verification_sessions_guard_insert() from public, anon, authenticated;
revoke execute on function public.verification_sessions_guard_update() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- verification_items: the validated challenge, its answer key and rubric (server-only)
-- ---------------------------------------------------------------------------
create table public.verification_items (
    id                      uuid primary key default gen_random_uuid(),
    -- V1: exactly one challenge per session.
    session_id              uuid not null unique references public.verification_sessions (id) on delete cascade,
    learner_id              uuid not null references public.profiles (id) on delete cascade,
    skill_id                uuid not null references public.skill_nodes (id),
    assessment_type         public.verification_assessment_type not null,
    grader_type             public.verification_grader_type not null,
    prompt                  text not null check (char_length(btrim(prompt)) between 20 and 4000),
    -- mcq: [{"key": "A", "text": "..."}, ...]; empty otherwise.
    choices                 jsonb not null default '[]'::jsonb check (jsonb_typeof(choices) = 'array'),
    -- The answer key: an mcq key set ("B" / "A,C"), a number, or a model answer. Never shown
    -- to the learner before grading.
    expected_answer         text check (expected_answer is null
                                        or char_length(btrim(expected_answer)) between 1 and 2000),
    -- [{"criterion": "...", "points": 1}, ...] (Appendix A.4).
    rubric                  jsonb not null default '[]'::jsonb check (jsonb_typeof(rubric) = 'array'),
    difficulty              double precision not null check (difficulty between 0 and 1),
    prerequisites_used      uuid[] not null default '{}',
    transfer_distance       text not null check (transfer_distance in ('near', 'medium', 'far')),
    estimated_minutes       smallint not null check (estimated_minutes between 1 and 120),
    generator_version       text not null check (generator_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    prompt_version          text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    -- The VERIFICATION_GENERATION run that produced the accepted challenge.
    generation_model_run_id uuid not null references public.model_runs (id),
    generation_attempt      smallint not null check (generation_attempt between 1 and 10),
    -- Fingerprint of the normalized prompt, and of the earlier prompts shown as history.
    prompt_fingerprint      text not null check (prompt_fingerprint ~ '^[0-9a-f]{64}$'),
    history_fingerprints    text[] not null default '{}',
    validator_version       text not null check (validator_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    -- The deterministic validator's report (the checks it passed).
    validation              jsonb not null check (jsonb_typeof(validation) = 'object'),
    created_at              timestamptz not null default now(),
    -- Every issued item has a grader: MCQ exact, numeric tolerance, or the rubric evaluator.
    -- code / sql have none in V1 (no sandbox), so they can never be issued.
    constraint verification_items_grader check (
        (assessment_type = 'mcq' and grader_type = 'MCQ_EXACT')
        or (assessment_type = 'numeric' and grader_type = 'NUMERIC_TOLERANCE')
        or (assessment_type in ('short_response', 'reasoning') and grader_type = 'RUBRIC_AI')),
    constraint verification_items_answer_key check (
        (assessment_type not in ('mcq', 'numeric') or expected_answer is not null)
        and (assessment_type <> 'mcq' or jsonb_array_length(choices) between 2 and 6)
        and (assessment_type = 'mcq' or jsonb_array_length(choices) = 0)
        and (assessment_type not in ('short_response', 'reasoning') or jsonb_array_length(rubric) between 1 and 10)),
    constraint verification_items_no_self_prerequisite check (not skill_id = any(prerequisites_used))
);

comment on table public.verification_items is
    'Validated verification challenge with its answer key and rubric (P6). Server-only: never readable by clients. Append-only.';

create index verification_items_learner_skill_idx on public.verification_items (learner_id, skill_id, created_at desc);

-- An item belongs to a PLANNED session and copies its learner and skill. Its difficulty is in
-- the planned band, and every prerequisite it uses is a known prerequisite of the skill.
create function public.verification_items_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    s record;
begin
    select vs.learner_id, vs.skill_id, vs.state, vs.failure_code, vs.difficulty_min, vs.difficulty_max
      into s from public.verification_sessions vs where vs.id = new.session_id;
    if not found then
        raise exception 'verification session % does not exist', new.session_id using errcode = '23503';
    end if;
    if s.state <> 'PLANNED' or s.failure_code is not null then
        raise exception 'a challenge is only added to a PLANNED session' using errcode = '23514';
    end if;
    if (s.learner_id, s.skill_id) is distinct from (new.learner_id, new.skill_id) then
        raise exception 'the challenge must target the session''s learner and skill' using errcode = '23514';
    end if;
    if new.difficulty < s.difficulty_min or new.difficulty > s.difficulty_max then
        raise exception 'challenge difficulty % is outside the planned band [%, %]',
            new.difficulty, s.difficulty_min, s.difficulty_max using errcode = '23514';
    end if;
    if exists (
        select 1 from unnest(new.prerequisites_used) as p(id)
         where not exists (select 1 from public.skill_edges e
                            where e.edge_type = 'PREREQUISITE' and e.from_skill_id = p.id
                              and e.to_skill_id = new.skill_id)) then
        raise exception 'the challenge uses a prerequisite the skill graph does not know' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger verification_items_guard
    before insert on public.verification_items
    for each row execute function public.verification_items_guard();

create trigger verification_items_append_only
    before update on public.verification_items
    for each row execute function public.reject_update();

revoke execute on function public.verification_items_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- verification_results: the immutable graded result (one per item)
-- ---------------------------------------------------------------------------
create table public.verification_results (
    id                       uuid primary key default gen_random_uuid(),
    -- Replay-safe: one graded result per challenge item.
    item_id                  uuid not null unique references public.verification_items (id) on delete cascade,
    session_id               uuid not null references public.verification_sessions (id) on delete cascade,
    learner_id               uuid not null references public.profiles (id) on delete cascade,
    skill_id                 uuid not null references public.skill_nodes (id),
    -- The graded response: exactly the session's submitted_response.
    response                 jsonb not null check (jsonb_typeof(response) = 'object'),
    score                    double precision not null check (score between 0 and 1),
    pass                     boolean not null,
    -- Deterministic from the grade: pass -> CORRECT (1); score 0 -> INCORRECT (0); otherwise
    -- PARTIAL with outcome = score.
    outcome_signal           public.outcome_signal not null,
    outcome                  double precision not null check (outcome between 0 and 1),
    -- Grader detail (parsed answer, criterion results, ...). Learner-facing feedback below.
    evaluation               jsonb not null check (jsonb_typeof(evaluation) = 'object'),
    feedback                 text not null check (char_length(btrim(feedback)) between 1 and 2000),
    -- B.2: 1.0 for a deterministic grader; the evaluator's confidence for AI rubric grading.
    grading_confidence       real not null check (grading_confidence between 0 and 1),
    evaluator_type           public.verification_evaluator_type not null,
    evaluator_version        text not null check (evaluator_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    evaluator_model_run_id   uuid references public.model_runs (id),
    evaluator_prompt_version text check (evaluator_prompt_version is null
                                         or evaluator_prompt_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    policy_snapshot          jsonb not null check (jsonb_typeof(policy_snapshot) = 'object'),
    processing_job_id        uuid,
    created_at               timestamptz not null default now(),
    constraint verification_results_outcome check (
        (outcome_signal = 'CORRECT' and outcome = 1 and pass)
        or (outcome_signal = 'INCORRECT' and outcome = 0 and not pass)
        or (outcome_signal = 'PARTIAL' and outcome > 0 and outcome < 1 and not pass)),
    constraint verification_results_score check (
        (outcome_signal = 'INCORRECT') = (score = 0)
        and (outcome_signal <> 'PARTIAL' or outcome = score)),
    constraint verification_results_evaluator check (
        (evaluator_type = 'DETERMINISTIC' and evaluator_model_run_id is null
            and evaluator_prompt_version is null and grading_confidence = 1 and score in (0, 1))
        or (evaluator_type = 'AI_RUBRIC' and evaluator_model_run_id is not null
            and evaluator_prompt_version is not null))
);

comment on table public.verification_results is
    'Immutable graded verification result (P6). Its evidence is the VERIFICATION EvidenceEvent with source_id = this id; the ledger is never written from here. Append-only.';

create index verification_results_session_idx on public.verification_results (session_id);
create index verification_results_learner_skill_idx on public.verification_results (learner_id, skill_id, created_at desc);

-- A result grades the item of a SUBMITTED session, copies its session / learner / skill and
-- grades exactly the stored submission.
create function public.verification_results_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    i record;
begin
    select vi.session_id, vi.learner_id, vi.skill_id, vs.state, vs.failure_code, vs.submitted_response
      into i
      from public.verification_items vi
      join public.verification_sessions vs on vs.id = vi.session_id
     where vi.id = new.item_id;
    if not found then
        raise exception 'verification item % does not exist', new.item_id using errcode = '23503';
    end if;
    if (i.session_id, i.learner_id, i.skill_id) is distinct from (new.session_id, new.learner_id, new.skill_id) then
        raise exception 'the result must copy its item''s session, learner and skill' using errcode = '23514';
    end if;
    if i.state <> 'SUBMITTED' or i.failure_code is not null then
        raise exception 'only a SUBMITTED session is graded (session is %)', i.state using errcode = '23514';
    end if;
    if new.response is distinct from i.submitted_response then
        raise exception 'the result grades exactly the submitted response' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger verification_results_guard
    before insert on public.verification_results
    for each row execute function public.verification_results_guard();

create trigger verification_results_append_only
    before update on public.verification_results
    for each row execute function public.reject_update();

revoke execute on function public.verification_results_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- VERIFICATION evidence names its graded result (the guard 0005 left to P6)
-- ---------------------------------------------------------------------------
-- source_type = 'VERIFICATION' evidence must be the evidence OF a verification result: the
-- learner and skill are the result's, the type is VERIFICATION by the STUDENT, the outcome
-- and grading confidence are the result's, the difficulty is the challenge's, and there is no
-- mapping / attribution uncertainty (both 1), so evidence_confidence = grading_confidence
-- (B.2). Replay safety is the 0005 unique (source_type, source_id, skill_id).
create function public.evidence_events_verification_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    v record;
begin
    if new.source_type <> 'VERIFICATION' then
        return new;
    end if;
    select r.learner_id, r.skill_id, r.outcome_signal, r.outcome, r.grading_confidence, vi.difficulty
      into v
      from public.verification_results r
      join public.verification_items vi on vi.id = r.item_id
     where r.id = new.source_id;
    if not found then
        raise exception 'VERIFICATION evidence must name its verification result (% not found)', new.source_id
            using errcode = '23514';
    end if;
    if (v.learner_id, v.skill_id) is distinct from (new.learner_id, new.skill_id) then
        raise exception 'VERIFICATION evidence must match its result''s learner and skill' using errcode = '23514';
    end if;
    if new.evidence_type <> 'VERIFICATION' or new.actor <> 'STUDENT' then
        raise exception 'a verification result is VERIFICATION evidence of the learner' using errcode = '23514';
    end if;
    if (new.outcome_signal, new.outcome, new.grading_confidence, new.difficulty)
       is distinct from (v.outcome_signal, v.outcome, v.grading_confidence, v.difficulty) then
        raise exception 'VERIFICATION evidence must copy its result''s grade and its challenge''s difficulty'
            using errcode = '23514';
    end if;
    if new.mapping_confidence <> 1 or new.attribution_confidence <> 1 then
        raise exception 'verification evidence has no mapping or attribution uncertainty' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger evidence_events_verification_guard
    before insert on public.evidence_events
    for each row execute function public.evidence_events_verification_guard();

revoke execute on function public.evidence_events_verification_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- skill_ledger: VERIFIED / NEEDS_REVERIFICATION become reachable (§10.3)
-- ---------------------------------------------------------------------------
-- Only the P4-era check is dropped (0006 itself is unchanged). In its place, both states
-- require a non-excluded, passed SkillMirror verification of the skill: the mean / support /
-- recency gates stay in the deterministic mastery engine, which is the only writer.
alter table public.skill_ledger drop constraint skill_ledger_no_verification_states_before_p6;

create function public.skill_ledger_verification_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.mastery_state in ('VERIFIED', 'NEEDS_REVERIFICATION') and not exists (
        select 1 from public.evidence_events e
         where e.learner_id = new.learner_id and e.skill_id = new.skill_id
           and e.source_type = 'VERIFICATION' and e.evidence_type = 'VERIFICATION'
           and e.outcome_signal = 'CORRECT' and not e.excluded) then
        raise exception '% needs a passed SkillMirror verification of skill %', new.mastery_state, new.skill_id
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger skill_ledger_verification_guard
    before insert or update on public.skill_ledger
    for each row execute function public.skill_ledger_verification_guard();

revoke execute on function public.skill_ledger_verification_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- policy_config: verification (§11.1, Appendix B; engineering defaults in ADR 0007)
-- ---------------------------------------------------------------------------
insert into public.policy_config (key, value, description) values
    ('verification',
     '{"planner": {"max_daily_unsolicited": 2, "cooldown_after_pass_days": 7,
                   "cooldown_after_fail_days": 1, "cooldown_after_abandon_days": 1,
                   "retry_after_generation_failure_hours": 24, "abandon_in_progress_after_days": 14},
       "difficulty": {"band_half_width": 0.15, "min": 0.1, "max": 0.9},
       "generation": {"max_attempts": 3,
                      "supported_assessment_types": ["mcq", "numeric", "short_response", "reasoning"],
                      "history_limit": 5, "duplicate_max_similarity": 0.8,
                      "min_prompt_chars": 20, "max_prompt_chars": 4000,
                      "min_estimated_minutes": 1, "max_estimated_minutes": 15},
       "grading": {"numeric_relative_tolerance": 0.005, "numeric_absolute_tolerance": 0.000001,
                   "max_response_chars": 4000, "short_response_max_chars": 1000,
                   "numeric_max_chars": 64, "rubric_pass_min_score": 0.7,
                   "min_ai_grading_confidence": 0.7},
       "reverification": {"min_contradicting_failures": 2,
                          "contradicting_evidence_types": ["INDEPENDENT_APPLICATION", "TRANSFER",
                                                           "EXECUTION_RESULT", "VERIFICATION"],
                          "contradicting_outcome_signals": ["INCORRECT"]}}',
     'Verification (§11, Appendix B): at most 2 unsolicited verifications per learner-day, cooldowns, difficulty band, initial generation + 2 regenerations, deterministic grading tolerances, and the conservative reverification (contradiction) rule.');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.verification_sessions enable row level security;
alter table public.verification_items enable row level security;
alter table public.verification_results enable row level security;

revoke all on table public.verification_sessions, public.verification_items, public.verification_results
    from anon, authenticated;

-- Learners read their own sessions and graded results. verification_items (answer keys,
-- rubrics) get no client privilege at all; the API serves a sanitized challenge.
grant select on table public.verification_sessions, public.verification_results to authenticated;

create policy verification_sessions_select_own on public.verification_sessions
    for select to authenticated using ((select auth.uid()) = learner_id);
create policy verification_results_select_own on public.verification_results
    for select to authenticated using ((select auth.uid()) = learner_id);
