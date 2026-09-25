-- 0006_mastery_debt.sql
-- SkillMirror P4: the Verified Skill Ledger (mastery) and AI Assistance Debt
-- (architecture §7.1, §7.2, §10.2-§10.4, Appendix B; ADR 0005).
--
-- * skill_ledger is a rebuildable projection/cache of evidence_events (the source of
--   truth). The backend recomputes a row deterministically - no model call - after
--   evidence commits; a replay or a rebuild produces the same values.
-- * Unknown is not weak: below the support gate a skill is UNKNOWN whatever its mean.
-- * VERIFIED / NEEDS_REVERIFICATION need SkillMirror verification evidence (P6). No such
--   evidence can exist yet (migration 0005 admits AI_ACTIVITY evidence only), and this
--   table refuses both states until P6 relaxes the check.
-- * AI Assistance Debt is unverified skill delegation, not an AI-usage count: it is zero
--   unless the guarded eligibility (repeated recent AI/SHARED delegation) passes.
--
-- Learners may SELECT their own ledger; only the backend writes.

create type public.mastery_state as enum (
    'UNKNOWN', 'EMERGING', 'DEVELOPING', 'DEMONSTRATED', 'VERIFIED', 'NEEDS_REVERIFICATION');

create table public.skill_ledger (
    learner_id                 uuid not null references public.profiles (id) on delete cascade,
    skill_id                   uuid not null references public.skill_nodes (id),
    -- Weighted Beta-style evidence model (§10.2).
    alpha                      double precision not null check (alpha > 0),
    beta                       double precision not null check (beta > 0),
    mastery_mean               double precision not null check (mastery_mean between 0 and 1),
    support                    double precision not null check (support >= 0),
    mastery_state              public.mastery_state not null,
    -- AI Assistance Debt (§10.4): 0 unless eligible; components explain the score.
    debt_score                 double precision not null default 0 check (debt_score between 0 and 100),
    debt_eligible              boolean not null default false,
    debt_components            jsonb not null default '{}'::jsonb check (jsonb_typeof(debt_components) = 'object'),
    evidence_count             integer not null check (evidence_count >= 0),
    performance_evidence_count integer not null check (performance_evidence_count >= 0),
    recent_delegation_count    integer not null check (recent_delegation_count >= 0),
    last_evidence_at           timestamptz,
    -- The instant recency was evaluated at, and the algorithm/policy that produced the row.
    computed_as_of             timestamptz not null,
    algorithm_version          text not null check (algorithm_version ~ '^[a-z0-9][a-z0-9._/-]{1,79}$'),
    policy_snapshot            jsonb not null check (jsonb_typeof(policy_snapshot) = 'object'),
    -- Incremented whenever a recomputation changes the row.
    ledger_version             integer not null default 1 check (ledger_version >= 1),
    created_at                 timestamptz not null default now(),
    updated_at                 timestamptz not null default now(),
    primary key (learner_id, skill_id),
    constraint skill_ledger_mean_matches check (abs(mastery_mean - alpha / (alpha + beta)) < 1e-6),
    constraint skill_ledger_debt_needs_eligibility check (debt_eligible or debt_score = 0),
    constraint skill_ledger_counts check (performance_evidence_count <= evidence_count),
    -- VERIFIED / NEEDS_REVERIFICATION require real verification evidence (P6).
    constraint skill_ledger_no_verification_states_before_p6 check (
        mastery_state not in ('VERIFIED', 'NEEDS_REVERIFICATION'))
);

comment on table public.skill_ledger is
    'Derived mastery + AI Assistance Debt per learner and skill: a rebuildable cache of evidence_events (architecture §7.2, §10).';

create index skill_ledger_skill_idx on public.skill_ledger (skill_id);

create trigger skill_ledger_set_updated_at
    before update on public.skill_ledger
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- policy_config: mastery model and debt (§10.2-§10.4, Appendix B)
-- ---------------------------------------------------------------------------
insert into public.policy_config (key, value, description) values
    ('mastery',
     '{"prior_alpha": 1.0, "prior_beta": 1.0, "recency_half_life_days": 180,
       "unknown_min_support": 1.0, "emerging_below_mean": 0.45,
       "demonstrated_min_mean": 0.70, "demonstrated_min_support": 3.0,
       "application_types": ["INDEPENDENT_APPLICATION", "TRANSFER", "EXECUTION_RESULT", "VERIFICATION"],
       "application_min_outcome": 1.0,
       "verified_min_mean": 0.80, "verified_min_support": 4.0, "verification_max_age_days": 180}',
     'Mastery (§10.2, §10.3): Beta(1,1) prior, 180-day recency half-life; UNKNOWN below support 1.0 is checked first.'),
    ('debt',
     '{"min_recent_delegations": 2, "recent_window_days": 30, "delegation_half_life_days": 14,
       "tau": 2.0, "actor_weights": {"AI": 1.0, "SHARED": 0.5},
       "delegation_evidence_types": ["OBSERVATION", "ASSISTED_ATTEMPT"],
       "min_evidence_confidence": 0.80, "learning_relevance_levels": ["high", "medium"],
       "trivial_reason_codes": ["TRIVIAL_UTILITY", "FACTUAL_LOOKUP"],
       "evidence_gap_support_target": 3.0,
       "verification_factor": {"recently_passed": 0.2, "unverified": 0.6, "failed_or_due": 1.0},
       "verification_recent_days": 30, "actionable_min_score": 15}',
     'AI Assistance Debt (§10.4): eligible only with >= 2 recent accepted high-confidence AI/SHARED delegations; 100 * pressure * gap * importance * confidence * verification factor.');

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.skill_ledger enable row level security;

revoke all on table public.skill_ledger from anon, authenticated;
grant select on table public.skill_ledger to authenticated;

create policy skill_ledger_select_own on public.skill_ledger
    for select to authenticated using ((select auth.uid()) = learner_id);
