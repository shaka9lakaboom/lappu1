-- pgTAP tests for migration 0002 (raw capture storage + processing_jobs).
-- Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(33);

-- Fixture identities: learners A and B (profiles come from the signup trigger).
insert into auth.users (id, email)
values
    ('00000000-0000-4000-8000-0000000000a1', 'p1a@test.invalid'),
    ('00000000-0000-4000-8000-0000000000b1', 'p1b@test.invalid');

-- Written as the table owner, the way the backend ingestion service writes.
insert into public.conversations (id, learner_id, source_provider, external_id, first_seen_at, last_seen_at)
values
    ('10000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a1', 'chatgpt', 'conv-a', now(), now()),
    ('10000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b1', 'chatgpt', 'conv-b', now(), now());

insert into public.raw_messages (
    id, learner_id, conversation_id, source_provider, source_method, external_message_id,
    message_index, role, content_text, content_format, content_hash, fingerprint, revision_index,
    captured_at, context_incomplete, client_event_uuid, client_event_id
) values
    ('20000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a1',
     '10000000-0000-4000-8000-0000000000a1', 'chatgpt', 'browser_extension', 'msg-a1', 0, 'user',
     'Explain binary search.', 'text', repeat('a', 64), repeat('1', 64), 0, now(), false,
     gen_random_uuid(), 'chatgpt:msg-a1:r0'),
    ('20000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b1',
     '10000000-0000-4000-8000-0000000000b1', 'chatgpt', 'browser_extension', 'msg-b1', 0, 'user',
     'Private to B.', 'text', repeat('b', 64), repeat('2', 64), 0, now(), false,
     gen_random_uuid(), 'chatgpt:msg-b1:r0');

insert into public.attachments (raw_message_id, learner_id, position, kind, filename)
values ('20000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a1', 0, 'file', 'hw.pdf');

insert into public.processing_jobs (job_type, entity_type, entity_id, learner_id)
values
    ('PROCESS_RAW_MESSAGE', 'raw_message', '20000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a1'),
    ('PROCESS_RAW_MESSAGE', 'raw_message', '20000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b1');

-- Schema ------------------------------------------------------------------
select has_table('public', 'conversations', 'conversations table exists');
select has_table('public', 'raw_messages', 'raw_messages table exists');
select has_table('public', 'attachments', 'attachments table exists');
select has_table('public', 'processing_jobs', 'processing_jobs table exists');
select ok(
    (select bool_and(relrowsecurity) from pg_class
      where oid in ('public.conversations'::regclass, 'public.raw_messages'::regclass,
                    'public.attachments'::regclass, 'public.processing_jobs'::regclass)),
    'RLS is enabled on every P1 table'
);
select is(
    (select state from public.processing_jobs where entity_id = '20000000-0000-4000-8000-0000000000a1'),
    'PENDING'::public.job_state,
    'processing jobs start PENDING'
);

-- Uniqueness / idempotency -----------------------------------------------
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-a1', 'user', 'Different text', 'text', repeat('c', 64), repeat('3', 64),
         0, now(), false, gen_random_uuid(), 'x') $$,
    '23505', null,
    'preferred identity (learner, provider, message id, revision) is unique'
);
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-a1', 'user', 'Explain binary search.', 'text', repeat('a', 64), repeat('3', 64),
         5, now(), false, gen_random_uuid(), 'x') $$,
    '23505', null,
    'same content under the same message id cannot be stored twice as another revision'
);
select lives_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-a1', 'user', 'Edited text', 'text', repeat('d', 64), repeat('4', 64),
         1, now(), false, gen_random_uuid(), 'chatgpt:msg-a1:r1') $$,
    'a new revision of the same message id is stored'
);
insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
    role, content_text, content_format, content_hash, fingerprint, revision_index, captured_at,
    context_incomplete, client_event_uuid, client_event_id)
values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
    'browser_extension', 'assistant', 'No id here.', 'text', repeat('e', 64), repeat('5', 64), 0, now(),
    false, gen_random_uuid(), 'fp:x');
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         role, content_text, content_format, content_hash, fingerprint, revision_index, captured_at,
         context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'assistant', 'No id here.', 'text', repeat('e', 64), repeat('5', 64), 0, now(),
         false, gen_random_uuid(), 'fp:x') $$,
    '23505', null,
    'fallback fingerprint is unique per learner when there is no message id'
);
select lives_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         role, content_text, content_format, content_hash, fingerprint, revision_index, captured_at,
         context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000b1', 'chatgpt',
         'browser_extension', 'assistant', 'No id here.', 'text', repeat('e', 64), repeat('5', 64), 0, now(),
         false, gen_random_uuid(), 'fp:x') $$,
    'the same fingerprint for another learner is a different message'
);
select throws_ok(
    $$ insert into public.conversations (learner_id, source_provider, external_id, first_seen_at, last_seen_at)
       values ('00000000-0000-4000-8000-0000000000a1', 'chatgpt', 'conv-a', now(), now()) $$,
    '23505', null,
    'a conversation is unique per (learner, provider, external id)'
);
insert into public.conversations (learner_id, source_provider, external_id, first_seen_at, last_seen_at)
values ('00000000-0000-4000-8000-0000000000a1', 'chatgpt', null, now(), now());
select throws_ok(
    $$ insert into public.conversations (learner_id, source_provider, external_id, first_seen_at, last_seen_at)
       values ('00000000-0000-4000-8000-0000000000a1', 'chatgpt', null, now(), now()) $$,
    '23505', null,
    'there is one id-less conversation container per learner and provider'
);
select throws_ok(
    $$ insert into public.processing_jobs (job_type, entity_type, entity_id, learner_id)
       values ('PROCESS_RAW_MESSAGE', 'raw_message', '20000000-0000-4000-8000-0000000000a1',
               '00000000-0000-4000-8000-0000000000a1') $$,
    '23505', null,
    'job creation is idempotent per (job type, entity)'
);

-- Provenance constraints -------------------------------------------------
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000b1', 'chatgpt',
         'browser_extension', 'msg-x', 'user', 'Cross-learner', 'text', repeat('f', 64), repeat('6', 64),
         0, now(), false, gen_random_uuid(), 'x') $$,
    '23503', null,
    'a message cannot be attached to another learner''s conversation'
);
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-y', 'user', 'No capture time', 'text', repeat('f', 64), repeat('6', 64),
         0, false, gen_random_uuid(), 'x') $$,
    '23502', null,
    'captured_at is required'
);
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-z', 'user', 'Bad hash', 'text', 'not-a-hash', repeat('6', 64),
         0, now(), false, gen_random_uuid(), 'x') $$,
    '23514', null,
    'content_hash must be a SHA-256 hex digest'
);
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         external_message_id, role, content_text, content_format, content_hash, fingerprint,
         revision_index, captured_at, context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'msg-w', 'system', 'Bad role', 'text', repeat('f', 64), repeat('6', 64),
         0, now(), false, gen_random_uuid(), 'x') $$,
    '22P02', null,
    'role is restricted to user / assistant'
);
select throws_ok(
    $$ update public.raw_messages set content_text = 'rewritten'
        where id = '20000000-0000-4000-8000-0000000000a1' $$,
    '55000', null,
    'raw_messages is append-only (no in-place updates)'
);
select throws_ok(
    $$ insert into public.attachments (raw_message_id, learner_id, position, kind)
       values ('20000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b1', 1, 'image') $$,
    '23503', null,
    'an attachment belongs to a message of the same learner'
);

-- RLS as learner A --------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000000a1", "role": "authenticated"}';

select is(
    (select count(*) from public.raw_messages where learner_id <> '00000000-0000-4000-8000-0000000000a1'),
    0::bigint,
    'learner A sees none of learner B''s raw messages'
);
select is((select count(*) from public.raw_messages), 3::bigint, 'learner A sees only their own raw messages');
select is((select count(*) from public.conversations), 2::bigint, 'learner A sees only their own conversations');
select is((select count(*) from public.attachments), 1::bigint, 'learner A sees only their own attachments');
select is((select count(*) from public.processing_jobs), 1::bigint, 'learner A sees only their own jobs');
select is(
    (select count(*) from public.activity_feed),
    3::bigint,
    'activity_feed shows only the caller''s messages (security_invoker RLS)'
);
select is(
    (select processing_state from public.activity_feed where id = '20000000-0000-4000-8000-0000000000a1'),
    'PENDING'::public.job_state,
    'activity_feed reports the processing state of a message'
);
select throws_ok(
    $$ insert into public.raw_messages (learner_id, conversation_id, source_provider, source_method,
         role, content_text, content_format, content_hash, fingerprint, revision_index, captured_at,
         context_incomplete, client_event_uuid, client_event_id)
       values ('00000000-0000-4000-8000-0000000000a1', '10000000-0000-4000-8000-0000000000a1', 'chatgpt',
         'browser_extension', 'user', 'forged', 'text', repeat('f', 64), repeat('7', 64), 0, now(),
         false, gen_random_uuid(), 'x') $$,
    '42501', null,
    'learners cannot write raw messages directly (ingestion goes through the API)'
);
select throws_ok(
    $$ delete from public.raw_messages $$,
    '42501', null,
    'learners cannot delete raw messages'
);
select throws_ok(
    $$ update public.processing_jobs set state = 'COMPLETED' $$,
    '42501', null,
    'learners cannot change processing job state'
);

-- anon --------------------------------------------------------------------
reset role;
set local role anon;
select throws_ok(
    $$ select count(*) from public.raw_messages $$,
    '42501', null,
    'anon cannot read raw messages'
);
select throws_ok(
    $$ select count(*) from public.activity_feed $$,
    '42501', null,
    'anon cannot read the activity feed'
);

reset role;
select is(
    (select count(*) from public.raw_messages where learner_id = '00000000-0000-4000-8000-0000000000a1'),
    3::bigint,
    'rejected writes left learner A''s raw messages unchanged'
);

select * from finish();
rollback;
