# 0002 — P1 capture + ingestion implementation decisions

- Status: accepted
- Date: 2026-09-25
- Scope: Phase P1 (architecture §6, §7.1–7.3, §13, §15.2). None of these change the frozen
  architecture. They record how it was implemented where the architecture left a choice.

## Backend and storage

1. **The backend writes to PostgreSQL directly (psycopg 3, `DATABASE_URL`).** A batch is one
   transaction: raw message revisions, attachment metadata and one `PROCESS_RAW_MESSAGE`
   job per new revision. The API acknowledges only after the commit (§13.1). A
   per-learner `pg_advisory_xact_lock` serializes concurrent batches from the same learner
   so revision numbering cannot race. Prepared statements are off so the Supabase
   transaction pooler also works. Without `DATABASE_URL`, ingestion fails closed (503).
2. **Identity comes only from the verified JWT.** Every envelope must carry the token's
   subject as `learner_id`, or the whole batch gets 403. Stored rows always use the token's
   subject. A valid token without a `profiles` row also gets 403.
3. **Idempotency is enforced by unique indexes, not by client state.** The indexes cover
   `(learner, provider, external_message_id, revision_index)` (§6.6 preferred identity),
   `(learner, provider, external_message_id, content_hash)` (the same text under the same
   id is the same revision) and `(learner, fingerprint)` when there is no message id (§6.6
   fallback). A resend returns `duplicate` with the stored id. If a client reuses a revision
   number for *different* text, for example after a page reload restarted its counter, the
   server stores it as `max(revision) + 1` and records the claimed number in
   `capture_metadata`.
4. **Fallback fingerprint details.** `normalized_content` is NFC with every whitespace run
   collapsed to one space and trimmed. The whitespace set is an explicit code-point list,
   because JS `\s` and Python `\s` differ. `coarse_timestamp` is the UTC hour of
   `occurred_at ?? captured_at`. TS and Python are checked against shared vectors
   (`packages/contracts/fixtures/fingerprint-vectors.json`).
5. **`raw_messages` is append-only.** A trigger rejects UPDATE. Rows are deleted only by
   cascade when the auth user is deleted. Learners get SELECT on their own rows only.
   `anon` gets nothing, and clients cannot INSERT, UPDATE or DELETE.
6. **`processing_jobs` has a nullable `learner_id`,** so learners can read their jobs' state
   through RLS. It also has a unique `(job_type, entity_id)`. P1 ships
   `claim_jobs` (FOR UPDATE SKIP LOCKED), `complete_job` and `fail_job` (backoff, FAILED
   after `max_attempts`, default 3, §15.4). No worker runs yet (P3).
7. **The Activity page reads `activity_feed` through RLS** instead of a `GET /v1/activity`
   endpoint. That endpoint is deferred until there is derived data to show. The view is
   `security_invoker` and exposes a 280-character preview, not the full text.

## Contract

8. **`message_index` was added to the §6.5 envelope.** It is the 0-based position in the
   rendered thread, carrying "ordering" (§6.4). `external_parent_message_id` is the
   previous rendered message, which is its parent in ChatGPT's visible branch.
9. **Capture metadata is batch-level** (`client.extension_version`, `client.adapter_version`)
   and is stored per row in `capture_metadata`. The frozen envelope fields are unchanged.
10. **Validation is strict:** unknown fields are rejected, strings, ints and bools are never
    coerced, and ids, model slugs and MIME types must match patterns. Content can be at most
    100 000 characters, a batch at most 50 events, a body at most 6 MB (413 before
    parsing). `content_hash` is recomputed and must match. Attachments without content
    require `context_incomplete = true`. P1 ingests only `chatgpt` / `browser_extension`.

## Extension

11. **The learner signs in to the Companion popup with their SkillMirror (Supabase)
    account.** The service worker calls Supabase Auth's public token endpoints with the
    anon key, the same trust level as the web app. No web bridge or `externally_connectable`
    is needed. The session lives in the extension origin's IndexedDB, which pages and
    content scripts cannot read. It is refreshed ahead of expiry, single-flight because
    refresh tokens rotate. No service-role key or secret can enter the bundle: `build.mjs`
    refuses them, and the manifest validator scans the output.
12. **Permissions: `storage` and `alarms` only.** There is one content script, on
    `https://chatgpt.com/*`. There are no `host_permissions`: the API is reached through
    CORS, and Supabase Auth allows any origin. The popup detects a supported page by
    pinging the active tab's content script, so it needs no `tabs` permission.
13. **The dev extension id is stable:** `cohpimnabjigooghbigblennedbplojm`, derived from the
    public key in `manifest.json` `key`. The private key was not kept, and unpacked loading
    does not need it. Local backend `.env`:
    `CORS_ORIGINS=http://localhost:3000,chrome-extension://cohpimnabjigooghbigblennedbplojm`.
    The Chrome Web Store id will be added to `CORS_ORIGINS` at release (P9). Wildcards
    are rejected at startup, and credentials mode is off (Bearer tokens only).
14. **The LocalQueue lives in the service worker's IndexedDB.** Items are deleted only when the
    response acknowledges them (`accepted` or `duplicate`). The flush triggers are enqueue,
    a one-minute alarm, the `online` event and "Sync now". Network errors, 429, 5xx and 403
    back off exponentially (5 s up to 10 min). A 401 refreshes the token once. A 422 or 413
    bisects the batch, and a single rejected event is quarantined locally. The 5 000 most
    recently acknowledged dedup keys are kept, so re-rendered history is not queued again.
15. **Capture rules.** A message is emitted when the provider marks it final (no
    `data-message-streaming`, no `pending-` id, no stop button) and its text has been stable
    for 1.5 s. A new chat waits up to 10 s for its conversation id. A changed message becomes
    the next revision. While paused nothing is captured, and a turn in flight when capture
    resumes is suppressed as well. Opening an existing conversation captures its visible
    messages once; the client and the server both deduplicate them. Text over the limit is
    truncated and flagged `context_incomplete`. It is never dropped silently.
16. **ChatGPT DOM support.** The logged-out ChatGPT shell (`li[data-message-role]`) was
    recorded from the real site. Fixtures: `apps/extension/tests/fixtures/chatgpt/lightweight-*`.
    The signed-in app layout (`[data-message-author-role]`) is covered by a **synthetic**
    fixture built from the publicly known structure. It must be replaced with a real capture
    when one is recorded.
