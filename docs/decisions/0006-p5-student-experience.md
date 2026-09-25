# 0006 — P5 student experience: explanations, corrections, recommendations

- Status: accepted
- Date: 2026-09-25
- Scope: P5 (architecture §5.1 Engine 16, §5.2 Feedback & Correction and Explanation Service,
  §7.1 `feedback` / `recommendations`, §7.2, §12.1 Dashboard / Skill Map / Skill Detail /
  Activity, §13, §16 "Feedback" and "UI"). Verification (P6, migration **0008**) and the teacher
  and admin views (P7) are out of scope.
- P5 is a read, explanation and correction layer over P2–P4 data. It makes **no model call**.
- Exit gate (§19): a learner can inspect the exact reason for every meaningful skill state and
  safely correct wrong or irrelevant evidence.

## Invariants this ADR enforces

| Invariant | Where it is enforced |
| --- | --- |
| Unknown is not weak | A skill without a ledger row is returned and shown as UNKNOWN (never 0%, no mean). The UI labels it "Not enough activity yet" in a neutral tone; no skill state uses red. No mean gate is shown while UNKNOWN. An UNKNOWN skill never gets PRACTICE/PREREQUISITE (engine + `recommendations_unknown_not_weak` check), and an UNKNOWN prerequisite is never a gap |
| AI use is not dependency | VERIFY needs *actionable* debt, which needs the P4 eligibility guards. Debt that is not eligible is band NONE: "There is not enough repeated AI delegation here to infer reliance." |
| Evidence precedes judgement | Every state, band and recommendation is derived from stored evidence and the ledger; corrections change the ledger only through evidence exclusion + recompute |
| Debt ≠ usage count; no moral judgement | The UI leads with a qualitative band and its factors; the 0–100 score sits behind "Internal detail" |
| Every judgement is explainable | `GET /v1/skills/{id}` returns the explanation code, gates, factors and every EvidenceEvent with spans, confidences, reason code and the chain to the captured messages |
| No irreversible AI truth; provenance kept | Corrections are the one-way `evidence_events.excluded` of 0005; nothing is deleted; mappings are never rewritten |

## Schema (migration 0007)

1. **Numbering.** 0001–0006 are on hosted and unchanged (a test pins their git blob ids). P5 is
   **0007**; P6 verification becomes **0008**.
2. **`feedback`** (§7.1: id, user_id, target_type/id, action, note, created_at, plus P5 fields).
   - Actions: `WRONG_SKILL`, `DONT_COUNT`, `EVALUATION`. Targets: `EVIDENCE_EVENT`,
     `SKILL_MAPPING`, `ACTIVITY_SEGMENT`, `SKILL`, `RECOMMENDATION`. WRONG_SKILL concerns a
     mapping (directly or via its evidence); DONT_COUNT may also cover a whole task unit;
     EVALUATION may concern anything the learner can see (`feedback_action_target` check).
   - `verdict` (`AGREE | DISAGREE | UNCLEAR`) is EVALUATION-only; an evaluation needs a verdict
     or a note (plain text, ≤ 2000 characters).
   - The guard trigger resolves `skill_id`, `mapping_id`, `segment_id` **from the target** (never
     from the client) and requires the target to be the learner's own. It also re-checks the
     recorded effect: every id in `excluded_evidence_ids` must be the learner's excluded
     captured-activity evidence inside the correction's scope.
   - An evaluation never changes evidence (`feedback_evaluation_no_effect`).
   - Append-only.
3. **Idempotency.** `(user_id, client_request_id)` is unique, and a learner has at most one
   `DONT_COUNT` / `WRONG_SKILL` per target (partial unique index). The request hash is stored:
   the same key with a different request is a 409.
4. **Corrections hold for later evidence.** A job deferred by the model quota can write evidence
   after the learner corrected the mapping or task unit. A `BEFORE INSERT` trigger on
   `evidence_events` (`evidence_events_apply_corrections`, firing before the 0005 guard) inserts
   such captured-activity evidence already excluded (`LEARNER_WRONG_SKILL` /
   `LEARNER_DONT_COUNT`). It is an insert-time value, not an update, so the 0005 append-only
   guard is untouched.
5. **`recommendations`** (§7.1: id, learner_id, skill_id, type, priority, reason_code, state).
   - Types are Engine 16's actions: `NO_ACTION | PRACTICE | VERIFY | PREREQUISITE | REVERIFY`.
     States: `ACTIVE | SUPERSEDED | COMPLETED | DISMISSED` (COMPLETED/DISMISSED become reachable
     with P6/P7).
   - It is a rebuildable projection of the ledger, not evidence. At most one ACTIVE row per
     learner and skill. A different action supersedes the row and inserts a new one; resolved
     rows are immutable; while ACTIVE only the priority, the mastery state read and the inputs
     may change (guard trigger).
   - Checks: PREREQUISITE names its prerequisite (`related_skill_id`); NO_ACTION has priority 0;
     PRACTICE/PREREQUISITE only for EMERGING/DEVELOPING; REVERIFY only for NEEDS_REVERIFICATION;
     UNKNOWN only NO_ACTION or VERIFY.
   - `inputs` stores the rule inputs (state, support, debt band, deferral, prerequisite gap,
     ledger version) so each recommendation stays explainable.
6. **Policy key `recommendations`**: `max_active_verify` 2 (Appendix B), `prerequisite_gap_states`
   `["EMERGING"]` (the validator refuses UNKNOWN), `debt_bands` `{moderate_min: 15, high_min: 25}`.
   A separate key, so the ledger's policy snapshot (mastery + debt) and its `ledger_version` do
   not churn.
7. **Access.** RLS on both tables; `authenticated` has SELECT on its own rows only; `anon` has
   nothing; the three trigger functions are not executable by clients. Feedback is written only
   by the backend after it authorizes the target; recommendations only by the deterministic
   refresh.

## Correction semantics (§16 "Feedback")

8. **Flow — one transaction:**

   ```
   DONT_COUNT / WRONG_SKILL:  feedback -> one-way exclusion of the evidence in scope
                              -> ledger recompute of the affected skills -> recommendation refresh
   EVALUATION:                feedback only
   ```

   - Scope: an evidence event or a skill mapping covers that **mapping** (the skill as mapped
     from that task unit); DONT_COUNT of an activity segment covers the **whole task unit**.
   - WRONG_SKILL does **not** invent a replacement skill; a remap stays a later workflow.
   - Only `AI_ACTIVITY` evidence can be corrected: verification, assessment and teacher evidence
     cannot be excluded by the learner (422).
   - Nothing is deleted: raw_messages, activity_segments, mapping_decisions, skill_mappings,
     attributions and evidence_events keep every row; the mapping's correction is the feedback
     row itself.
   - Recompute covers the skills that have captured-activity evidence in scope, so a correction
     of a mapping without evidence creates no empty ledger row.
9. **Concurrency.** The learner's feedback is serialized (`feedback:{learner}`), and the segment's
   `attribution:{segment}` lock (taken by the pipeline before it writes evidence) orders a
   correction against a job writing the same task unit. Lock order is feedback → attribution →
   ledger → recommendations; the pipeline never holds two of them at once, so there is no cycle.
10. **The response** carries the recomputed ledger rows and refreshed recommendations; the web
    action then refreshes the page from that state.

## Engine 16 — recommendations (deterministic, no model call)

11. **Rules, first match wins** (`app/intelligence/recommendations/engine.py`):

    | Condition | Action | Reason | Priority |
    | --- | --- | --- | --- |
    | NEEDS_REVERIFICATION | REVERIFY | VERIFICATION_STALE | 80 + 10·importance |
    | actionable debt (eligible, ≥ 15), within the top `max_active_verify` by debt | VERIFY | REPEATED_DELEGATION_UNVERIFIED | 60 + 19·score/100 |
    | EMERGING/DEVELOPING with a prerequisite in a gap state | PREREQUISITE | PREREQUISITE_GAP | 50 + 10·importance |
    | EMERGING (performance evidence exists) | PRACTICE | EMERGING_NEEDS_PRACTICE | 40 + 10·importance |
    | DEVELOPING (performance evidence exists) | PRACTICE | DEVELOPING_NEEDS_PRACTICE | 30 + 10·importance |
    | DEMONSTRATED / VERIFIED | NO_ACTION | INDEPENDENT_EVIDENCE_SUFFICIENT / RECENTLY_VERIFIED | 0 |
    | UNKNOWN | NO_ACTION | NOT_ENOUGH_EVIDENCE (gather more evidence) | 0 |

    - Rounding is half-up; ties break by skill id, so the queue is a pure function of the
      ledger.
    - The weakest gap prerequisite is named. VERIFIED/NEEDS_REVERIFICATION are unreachable before
      P6 (ADR 0005 §19) but already handled.
12. **Burden cap.** At most `max_active_verify` VERIFY recommendations at a time; further
    actionable skills fall through to the next rule and are marked `verify_deferred`. This is
    P5's form of Appendix B's "2 verification recommendations per learner"; the daily issuance
    budget of challenges belongs to the P6 planner.
13. **Refresh.** `refresh_recommendations` re-derives the learner's whole scope (active course
    skills plus every ledger row) under `recommendations:{learner}`, writes only on change, and
    supersedes rows of skills that left the scope. It runs after the evidence stage's ledger
    recompute, after every correction, and before `GET /v1/recommendations` and
    `GET /v1/skills/{id}` are read (a cache fill, so the queue always matches the ledger,
    including for ledgers written before 0007).

## Explanation Service

14. `app/intelligence/explanation.py`:
    - **Mastery explanation codes**:
      - UNKNOWN: NO_EVIDENCE, NO_INDEPENDENT_PERFORMANCE (only exposure/observation),
        NOT_ENOUGH_SUPPORT
      - EMERGING: EARLY_DIFFICULTY
      - DEVELOPING: MIXED_RESULTS, NEEDS_MORE_EVIDENCE, NEEDS_INDEPENDENT_APPLICATION
      - DEMONSTRATED: INDEPENDENT_EVIDENCE_SUPPORTS
      - VERIFIED: RECENT_VERIFICATION
      - NEEDS_REVERIFICATION: VERIFICATION_STALE
    - **Gates** (enough evidence, strong results, sustained evidence, independent application).
      While UNKNOWN only the evidence gate is returned.
    - **Debt band**: NONE unless eligible, then LOW / MODERATE / HIGH by the policy bands.
    - **Factor levels** (thirds) for delegation pressure, evidence gap, importance, confidence and
      verification state.
    - The skill detail re-derives per-evidence weights at the ledger's `computed_as_of`, so the
      "Why?" matches the stored row.

## API (§13)

15. New endpoints; each scopes by the authenticated learner **explicitly** (never RLS alone):
    - `GET /v1/skills/{skill_id}`: 404 outside the learner's courses, evidence and ledger; a topic
      is 404.
    - `GET /v1/activity[?limit&before&raw_message_id…]`: one row per captured message (as the P1
      `activity_feed` view, which stays). The turn's anchor carries its segments: route, mapping
      outcome, mapped skills with actor, evidence type, exclusion and correction. Pages never split
      a capture batch (the boundary extends over one `received_at`).
    - `POST /v1/feedback`: `Idempotency-Key` required; 201 created, 200 replay, 404 foreign or
      missing target, 409 key reuse, 422 invalid combination or non-correctable evidence.
    - `GET /v1/recommendations[?course_id&include_no_action&limit]`: NO_ACTION is omitted by
      default.
16. `GET /v1/ledger` is extended with `debt_band` only (`LedgerEntry` is its P5 name).
    `EvidenceEvent` gains its provenance and exclusion fields.
17. **Never returned:** prompts, policy snapshots, model output, provider details. Captured text
    appears only as spans and short previews, rendered as plain text.

## Web (§12.1)

18. **Pages:**
    - `/dashboard`: course cards with state counts, the selected course's tiles (UNKNOWN first;
      VERIFIED / re-verification marked "after SkillMirror checks"), top recommendations and
      empty states. The account and API cards keep their test ids, and the learning section
      degrades to a notice without the API.
    - `/skills`: topic → skill → state; a missing ledger row is UNKNOWN.
    - `/skills/[skillId]`: mastery with "Why?", recommended action, debt panel, evidence timeline
      with per-event "Why?", prerequisites, and an evaluation form.
    - `/activity`: chips, actor, route outcome, evidence state, corrections. It falls back to the
      P1 list if the API is down.
19. **Corrections** ask for confirmation first (they are one-way) and use one idempotency key per
    form instance. The server action posts to the backend, which recomputes before answering,
    then `refresh()` re-renders from the recomputed state.
20. **Course selector:** a UI cookie (`sm_course`), validated against the learner's courses on
    every read, with `?course=` for links. It never changes evidence or the extension's
    `active_course_id`.
21. **States:** loading (`loading.tsx`) and error (`error.tsx`) boundaries for every student route.

## Zero model calls

22. P5 code never constructs the ModelGateway. A test imports every P5 module in a fresh
    interpreter and asserts that no `app.model_gateway` / Google SDK module is loaded. The DB
    tests assert that `model_runs` never grows across all P5 endpoints and corrections. The
    acceptance runs the backend without `GEMINI_API_KEY` and checks `model_runs` before and after.

## Known limitations

- A learner can exclude any of their captured-activity evidence, including unfavourable
  evidence. That is the architecture's "don't count this". Every correction is kept as an
  append-only feedback row with its effect (the audit trail for P7).
- Corrections cannot be undone (one-way by design). There is no remap after WRONG_SKILL yet, and
  a recommendation cannot yet be dismissed (the state exists).
- `GET /v1/recommendations` and `GET /v1/skills/{id}` refresh the derived queue before reading.
  This is an idempotent cache write inside a GET.
- The VERIFY cap is concurrent, not daily; the daily challenge budget is P6's.
- Debt bands (15/25) and the factor thirds are engineering defaults in policy, to be calibrated
  with the benchmark (P8).
- Activity rows are per message; the answer of an analysed turn points to its question instead
  of repeating its chips.
