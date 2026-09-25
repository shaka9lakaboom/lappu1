-- pgTAP tests for migration 0003 (courses, skill registry, embeddings, model_runs,
-- policy_config and the P3A analysis provenance tables).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(49);

-- Fixtures, written as the table owner (the way the backend writes) -------
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000003a1', 'p2a@test.invalid'),
    ('00000000-0000-4000-8000-0000000003b1', 'p2b@test.invalid');

insert into public.courses (id, owner_id, name, level)
values
    ('30000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000003a1', 'Intro to Python', 'Beginner'),
    ('30000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000003b1', 'Private course of B', null);
insert into public.course_memberships (course_id, user_id)
values
    ('30000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000003a1'),
    ('30000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000003b1');

insert into public.skill_nodes (id, slug, canonical_name, normalized_name, description, node_kind, status, source)
values
    ('40000000-0000-4000-8000-000000000001', 'p2t-for-loops', 'P2T For Loops', 'p2t for loop',
     'Write for loops that iterate over sequences.', 'SKILL', 'ACTIVE', 'SEED'),
    ('40000000-0000-4000-8000-000000000002', 'p2t-while-loops', 'P2T While Loops', 'p2t while loop',
     'Write while loops with correct termination.', 'SKILL', 'ACTIVE', 'SEED'),
    ('40000000-0000-4000-8000-000000000003', 'p2t-control-flow', 'P2T Control Flow', 'p2t control flow',
     'Direct program execution with conditions and loops.', 'TOPIC', 'ACTIVE', 'SEED'),
    ('40000000-0000-4000-8000-000000000004', 'p2t-hidden-candidate', 'P2T Hidden Candidate', 'p2t hidden candidate',
     'A concept that has not been approved yet.', 'SKILL', 'CANDIDATE', 'SEED');

insert into public.skill_aliases (skill_id, alias, normalized_alias, alias_kind, source)
values ('40000000-0000-4000-8000-000000000001', 'P2T for-loop iteration', 'p2t for loop iteration', 'VARIANT', 'SEED');

insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source)
values ('40000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000001', 'PARENT', 'SEED');

insert into public.course_skills (course_id, skill_id, importance, source, graph_version)
values
    ('30000000-0000-4000-8000-0000000000a1', '40000000-0000-4000-8000-000000000001', 0.8, 'COURSE_BOOTSTRAP', 1),
    ('30000000-0000-4000-8000-0000000000a1', '40000000-0000-4000-8000-000000000002', 0.5, 'COURSE_BOOTSTRAP', 1),
    ('30000000-0000-4000-8000-0000000000a1', '40000000-0000-4000-8000-000000000003', 0.5, 'COURSE_BOOTSTRAP', 1),
    ('30000000-0000-4000-8000-0000000000b1', '40000000-0000-4000-8000-000000000002', 0.5, 'COURSE_BOOTSTRAP', 1);

insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version, input_hash, latency_ms, status)
values ('33000000-0000-4000-8000-000000000001', 'job:test', 'SKILL_MAPPING', 'google', 'gemini-3.7-flash',
        'skill-mapping/v1', repeat('d', 64), 120, 'SUCCEEDED');

insert into public.skill_embeddings (skill_id, model, graph_version, content_hash, input_version, embedding)
values ('40000000-0000-4000-8000-000000000001', 'gemini-embedding-2', 1, repeat('e', 64),
        'skill-embedding-text/v1', array_fill(0.036::real, array[768])::extensions.vector);

insert into public.conversations (id, learner_id, source_provider, external_id, first_seen_at, last_seen_at)
values
    ('31000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000003a1', 'chatgpt', 'p2-conv-a', now(), now()),
    ('31000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000003b1', 'chatgpt', 'p2-conv-b', now(), now());
insert into public.raw_messages (
    id, learner_id, conversation_id, source_provider, source_method, external_message_id,
    external_parent_message_id, message_index, role, content_text, content_format, content_hash,
    fingerprint, revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id
) values
    ('32000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000003a1',
     '31000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p2-msg-a1', null, 0, 'user',
     'How do I loop over a list?', 'text', repeat('a', 64), repeat('4', 64), 0, now(), false,
     gen_random_uuid(), 'chatgpt:p2-msg-a1:r0'),
    ('32000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000003a1',
     '31000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'p2-msg-a2', 'p2-msg-a1', 1,
     'assistant', 'Use a for loop: for item in items: ...', 'text', repeat('b', 64), repeat('5', 64), 0,
     now(), false, gen_random_uuid(), 'chatgpt:p2-msg-a2:r0'),
    ('32000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000003b1',
     '31000000-0000-4000-8000-0000000000b1', 'chatgpt', 'browser_extension', 'p2-msg-b1', null, 0, 'user',
     'Private to B.', 'text', repeat('c', 64), repeat('6', 64), 0, now(), false,
     gen_random_uuid(), 'chatgpt:p2-msg-b1:r0');

insert into public.activity_segments (
    id, learner_id, conversation_id, anchor_message_id, user_message_id, assistant_message_id,
    source_message_ids, segment_index, segment_count, text, context, intent, learning_relevance,
    relevance_confidence, skill_bearing, skill_bearing_confidence, reason_code, route, route_reason,
    context_incomplete, course_ids, qualification_model_run_id, prompt_version, analysis_version
) values (
    '34000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000003a1',
    '31000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a1',
    '32000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a2',
    array['32000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a2']::uuid[],
    0, 1, 'How do I loop over a list?', 'academic', 'learn', 'high', 0.9, true, 0.9, 'CONCEPT_QUESTION',
    'MAP', 'LEARNING_SKILL_BEARING', false, array['30000000-0000-4000-8000-0000000000a1']::uuid[],
    '33000000-0000-4000-8000-000000000001', 'relevance-intent/v1', 'p3a-v1'
);
insert into public.mapping_decisions (
    id, segment_id, learner_id, outcome, retrieval_candidates, mapper_candidate_ids,
    mapping_model_run_id, prompt_versions, policy_snapshot, mapper_version
) values (
    '35000000-0000-4000-8000-000000000001', '34000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-0000000003a1', 'MAPPED', '[]'::jsonb,
    array['40000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000004']::uuid[],
    '33000000-0000-4000-8000-000000000001', '{}'::jsonb, '{}'::jsonb, 'mapper/p3a-v1'
);
insert into public.skill_mappings (
    decision_id, segment_id, learner_id, skill_id, status, confidence, first_pass_confidence,
    reason_code, status_reason, evidence_span, mapper_version
) values (
    '35000000-0000-4000-8000-000000000001', '34000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-0000000003a1', '40000000-0000-4000-8000-000000000001', 'ACCEPTED', 0.91, 0.91,
    'CONCEPT_USE', 'FIRST_PASS_ACCEPTED', 'loop over a list', 'mapper/p3a-v1'
);

-- Schema ------------------------------------------------------------------
select is(
    (select count(*)::int from pg_tables where schemaname = 'public' and tablename in (
        'model_runs', 'policy_config', 'courses', 'course_memberships', 'skill_nodes', 'skill_aliases',
        'skill_edges', 'course_skills', 'skill_embeddings', 'activity_segments', 'skill_candidates',
        'mapping_decisions', 'skill_mappings')),
    13,
    'all P2/P3A tables exist'
);
select ok(
    (select bool_and(c.relrowsecurity) from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relkind = 'r'),
    'RLS is enabled on every public table'
);
select is(
    (select count(*)::int from pg_tables where schemaname = 'public' and tablename in (
        'verification_sessions', 'verification_items', 'verification_results')),
    0,
    'no verification tables exist yet (P6 / 0008; evidence and ledger are 0005/0006, recommendations 0007)'
);
select is(
    (select atttypmod from pg_attribute
      where attrelid = 'public.skill_embeddings'::regclass and attname = 'embedding'),
    768,
    'skill embeddings are 768-dimensional pgvector columns'
);
select ok(
    exists (select 1 from pg_indexes where tablename = 'skill_embeddings'
             and indexdef ilike '%using hnsw%vector_cosine_ops%'),
    'an HNSW cosine index backs vector retrieval'
);
select ok(
    exists (select 1 from pg_indexes where tablename = 'skill_nodes'
             and indexdef ilike '%using gin%search_document%'),
    'a GIN full-text index backs lexical retrieval'
);

-- policy_config seeds (architecture §9.3, §9.4, Appendix B) ---------------
select is(
    (select value -> 'weights' from public.policy_config where scope_type = 'global' and key = 'retrieval'),
    '{"semantic": 0.55, "lexical": 0.30, "course_prior": 0.15}'::jsonb,
    'retrieval weights are 0.55 semantic / 0.30 lexical / 0.15 course prior'
);
select is(
    (select array[(value ->> 'pool_size')::int, (value ->> 'rerank_size')::int]
       from public.policy_config where key = 'retrieval'),
    array[20, 8],
    'retrieval pool is top 20, rerank keeps top 8'
);
select is(
    (select array[(value ->> 'accept_threshold')::numeric, (value ->> 'adjudicate_min')::numeric]
       from public.policy_config where key = 'mapping'),
    array[0.80, 0.65]::numeric[],
    'mapping gate: accept >= 0.80, second pass from 0.65'
);

-- Registry identity -----------------------------------------------------
select is(
    (select alias_kind from public.skill_aliases where normalized_alias = 'p2t for loop'),
    'CANONICAL'::public.skill_alias_kind,
    'the canonical name is registered as the CANONICAL alias automatically'
);
select throws_ok(
    $$ insert into public.skill_nodes (slug, canonical_name, normalized_name, description, source)
       values ('p2t-for-loops-2', 'P2T FOR-LOOPS', 'p2t for loop', 'Duplicate identity by spelling.', 'SEED') $$,
    '23505', null,
    'a capitalization/spelling variant cannot create a second skill identity'
);
select throws_ok(
    $$ insert into public.skill_aliases (skill_id, alias, normalized_alias, source)
       values ('40000000-0000-4000-8000-000000000002', 'P2T for loop iteration', 'p2t for loop iteration', 'SEED') $$,
    '23505', null,
    'an alias key resolves to exactly one canonical skill UUID'
);
select throws_ok(
    $$ update public.skill_nodes set id = gen_random_uuid() where id = '40000000-0000-4000-8000-000000000002' $$,
    '55000', null,
    'skill UUIDs are immutable'
);
update public.skill_nodes set canonical_name = 'P2T While Loop Termination', normalized_name = 'p2t while loop termination'
 where id = '40000000-0000-4000-8000-000000000002';
select is(
    (select version from public.skill_nodes where id = '40000000-0000-4000-8000-000000000002'),
    2,
    'a rename keeps the UUID and bumps the node version'
);
select is(
    (select skill_id from public.skill_aliases where normalized_alias = 'p2t while loop' and alias_kind = 'PREVIOUS_NAME'),
    '40000000-0000-4000-8000-000000000002'::uuid,
    'the old name still resolves to the same UUID as PREVIOUS_NAME'
);
select ok(
    (select search_document @@ to_tsquery('english', 'iteration')
       from public.skill_nodes where id = '40000000-0000-4000-8000-000000000001'),
    'aliases are part of the full-text search document'
);
select throws_ok(
    $$ update public.skill_nodes set status = 'MERGED' where id = '40000000-0000-4000-8000-000000000002' $$,
    '23514', null,
    'a MERGED node must point at its surviving skill'
);
select throws_ok(
    $$ insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source)
       values ('40000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000001', 'RELATED', 'SEED') $$,
    '23514', null,
    'RELATED edges are stored once, ordered'
);
select throws_ok(
    $$ insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source)
       values ('40000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001', 'PREREQUISITE', 'SEED') $$,
    '23514', null,
    'a skill cannot be its own prerequisite'
);

-- model_runs --------------------------------------------------------------
select throws_ok(
    $$ insert into public.model_runs (trace_id, task_type, provider, model, input_hash, latency_ms, status)
       values ('t', 'SKILL_MAPPING', 'google', 'gemini-3.7-flash', repeat('d', 64), 1, 'SUCCEEDED') $$,
    '23502', null,
    'a model run without prompt_version is rejected'
);
select throws_ok(
    $$ update public.model_runs set status = 'FAILED' $$,
    '55000', null,
    'model_runs is append-only'
);
insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000003c1', 'p2c@test.invalid');
insert into public.model_runs (trace_id, task_type, provider, model, prompt_version, input_hash, latency_ms, status, learner_id)
values ('job:c', 'EMBED_QUERY', 'google', 'gemini-embedding-2', 'retrieval-query/v1', repeat('f', 64), 5, 'SUCCEEDED',
        '00000000-0000-4000-8000-0000000003c1');
select lives_ok(
    $$ delete from auth.users where id = '00000000-0000-4000-8000-0000000003c1' $$,
    'deleting an account nulls its model_runs reference instead of being blocked'
);

-- Analysis provenance ---------------------------------------------------
select throws_ok(
    $$ insert into public.activity_segments (learner_id, conversation_id, anchor_message_id,
         source_message_ids, segment_index, segment_count, text, reason_code, route, route_reason,
         context_incomplete, prompt_version, analysis_version)
       values ('00000000-0000-4000-8000-0000000003b1', '31000000-0000-4000-8000-0000000000b1',
         '32000000-0000-4000-8000-0000000000a1', array['32000000-0000-4000-8000-0000000000a1']::uuid[],
         0, 1, 'x', 'X_CODE', 'STOP', 'NON_LEARNING', false, 'relevance-intent/v1', 'p3a-cross') $$,
    '23503', null,
    'a segment must trace to a raw message of the same learner'
);
select throws_ok(
    $$ insert into public.activity_segments (learner_id, conversation_id, anchor_message_id,
         source_message_ids, segment_index, segment_count, text, reason_code, route, route_reason,
         context_incomplete, prompt_version, analysis_version)
       values ('00000000-0000-4000-8000-0000000003a1', '31000000-0000-4000-8000-0000000000a1',
         '32000000-0000-4000-8000-0000000000a1', array['32000000-0000-4000-8000-0000000000a1']::uuid[],
         0, 1, 'x', 'X_CODE', 'STOP', 'NON_LEARNING', false, 'relevance-intent/v1', 'p3a-v1') $$,
    '23505', null,
    'one analysis per (anchor message, analysis version, segment): job replays cannot duplicate it'
);
select throws_ok(
    $$ insert into public.skill_mappings (decision_id, segment_id, learner_id, skill_id, status,
         confidence, first_pass_confidence, reason_code, status_reason, mapper_version)
       values ('35000000-0000-4000-8000-000000000001', '34000000-0000-4000-8000-000000000001',
         '00000000-0000-4000-8000-0000000003a1', '40000000-0000-4000-8000-000000000002', 'ACCEPTED',
         0.9, 0.9, 'CONCEPT_USE', 'FIRST_PASS_ACCEPTED', 'mapper/p3a-v1') $$,
    '23514', null,
    'a mapping must use a skill id supplied by retrieval to the mapper'
);
select throws_ok(
    $$ insert into public.skill_mappings (decision_id, segment_id, learner_id, skill_id, status,
         confidence, first_pass_confidence, reason_code, status_reason, mapper_version)
       values ('35000000-0000-4000-8000-000000000001', '34000000-0000-4000-8000-000000000001',
         '00000000-0000-4000-8000-0000000003a1', '40000000-0000-4000-8000-000000000004', 'ACCEPTED',
         0.9, 0.9, 'CONCEPT_USE', 'FIRST_PASS_ACCEPTED', 'mapper/p3a-v1') $$,
    '23514', null,
    'a mapping can never target a non-ACTIVE (candidate) skill'
);
select throws_ok(
    $$ update public.activity_segments set route = 'STOP' $$,
    '55000', null,
    'activity_segments is append-only'
);
select throws_ok(
    $$ update public.skill_mappings set confidence = 0.1 $$,
    '55000', null,
    'skill_mappings is append-only'
);
select throws_ok(
    $$ insert into public.mapping_decisions (segment_id, learner_id, outcome, abstain_reason, retrieval_candidates,
         prompt_versions, policy_snapshot, mapper_version)
       values ('34000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000003a1',
         'ABSTAINED', 'LOW_CONFIDENCE', '[]', '{}', '{}', 'mapper/p3a-v1') $$,
    '23505', null,
    'one mapping decision per segment'
);
select is(
    (select source_message_ids from public.activity_segments where id = '34000000-0000-4000-8000-000000000001'),
    array['32000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a2']::uuid[],
    'a segment keeps the raw user + assistant message ids it came from'
);

-- RLS as learner A --------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000003a1", "role": "authenticated"}';

select is((select count(*) from public.courses), 1::bigint, 'learner A sees only their own course');
select is(
    (select count(*) from public.courses where id = '30000000-0000-4000-8000-0000000000b1'),
    0::bigint,
    'learner A cannot read learner B''s course'
);
select is((select count(*) from public.course_memberships), 1::bigint, 'learner A sees only their own membership');
select is(
    (select count(*) from public.course_skills),
    3::bigint,
    'learner A sees the skill overlay of their own course only'
);
select is(
    (select count(*) from public.skill_nodes where id = '40000000-0000-4000-8000-000000000004'),
    0::bigint,
    'CANDIDATE registry nodes are not client-visible'
);
select ok(
    (select count(*) from public.skill_nodes where id = '40000000-0000-4000-8000-000000000001') = 1
    and (select count(*) from public.skill_aliases where skill_id = '40000000-0000-4000-8000-000000000001') >= 2,
    'ACTIVE registry nodes and their aliases are readable'
);
select is(
    (select count(*) from public.activity_segments) + (select count(*) from public.mapping_decisions)
      + (select count(*) from public.skill_mappings),
    3::bigint,
    'learner A reads their own segment, decision and mapping'
);
select throws_ok($$ select count(*) from public.skill_embeddings $$, '42501', null,
    'embeddings are server-only');
select throws_ok($$ select count(*) from public.model_runs $$, '42501', null,
    'model runs are server-only');
select throws_ok($$ select count(*) from public.policy_config $$, '42501', null,
    'policy_config is server-only');
select throws_ok($$ select count(*) from public.skill_candidates $$, '42501', null,
    'skill candidates are server-only');
select throws_ok(
    $$ insert into public.skill_nodes (slug, canonical_name, normalized_name, description, source)
       values ('forged', 'Forged Skill', 'forged skill', 'Client-minted identity.', 'MANUAL') $$,
    '42501', null,
    'clients cannot mint canonical registry skills'
);
select throws_ok(
    $$ update public.skill_aliases set skill_id = '40000000-0000-4000-8000-000000000002' $$,
    '42501', null,
    'clients cannot re-point aliases'
);
select throws_ok(
    $$ insert into public.courses (owner_id, name) values ('00000000-0000-4000-8000-0000000003a1', 'Direct') $$,
    '42501', null,
    'courses are created through the API, not directly'
);
select throws_ok(
    $$ insert into public.course_memberships (course_id, user_id)
       values ('30000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000003a1') $$,
    '42501', null,
    'learners cannot join another learner''s course by writing memberships'
);

-- RLS as learner B --------------------------------------------------------
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000003b1", "role": "authenticated"}';
select is(
    (select count(*) from public.activity_segments) + (select count(*) from public.skill_mappings),
    0::bigint,
    'learner B sees none of learner A''s analysis'
);
select is(
    (select count(*) from public.course_skills where course_id = '30000000-0000-4000-8000-0000000000a1'),
    0::bigint,
    'learner B cannot read learner A''s course overlay'
);

-- anon --------------------------------------------------------------------
reset role;
set local role anon;
select throws_ok($$ select count(*) from public.courses $$, '42501', null, 'anon cannot read courses');
select throws_ok($$ select count(*) from public.skill_nodes $$, '42501', null, 'anon cannot read the registry');

select * from finish();
rollback;
