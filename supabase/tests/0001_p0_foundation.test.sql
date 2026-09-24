-- pgTAP tests for migration 0001. Run against a local stack: `supabase test db`.
begin;
create extension if not exists pgtap with schema extensions;

select plan(13);

-- Fixture identities (inserted the same way the Auth service inserts them).
insert into auth.users (id, email, raw_user_meta_data)
values
    ('00000000-0000-4000-8000-00000000000a', 'a@test.invalid', '{"display_name": "  Learner A  "}'),
    ('00000000-0000-4000-8000-00000000000b', 'b@test.invalid', '{"role": "ADMIN"}');

-- Schema ------------------------------------------------------------------
select ok(
    exists (select 1 from pg_extension where extname = 'vector'),
    'pgvector extension is enabled'
);
select ok(
    (select relrowsecurity from pg_class where oid = 'public.profiles'::regclass),
    'RLS is enabled on public.profiles'
);

-- Signup trigger ----------------------------------------------------------
select is(
    (select count(*) from public.profiles
      where id in ('00000000-0000-4000-8000-00000000000a', '00000000-0000-4000-8000-00000000000b')),
    2::bigint,
    'a profile is created for every new auth user'
);
select is(
    (select role from public.profiles where id = '00000000-0000-4000-8000-00000000000a'),
    'STUDENT'::public.app_role,
    'new profiles default to STUDENT'
);
select is(
    (select role from public.profiles where id = '00000000-0000-4000-8000-00000000000b'),
    'STUDENT'::public.app_role,
    'client-supplied role metadata is ignored'
);
select is(
    (select display_name from public.profiles where id = '00000000-0000-4000-8000-00000000000a'),
    'Learner A',
    'display_name is taken from signup metadata and trimmed'
);

-- RLS as learner A --------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-00000000000a", "role": "authenticated"}';

select is(
    (select count(*) from public.profiles),
    1::bigint,
    'an authenticated user sees only their own profile'
);

update public.profiles set display_name = 'Renamed A' where id = '00000000-0000-4000-8000-00000000000a';
update public.profiles set display_name = 'Hijacked' where id = '00000000-0000-4000-8000-00000000000b';

select throws_ok(
    $$ update public.profiles set role = 'ADMIN' where id = '00000000-0000-4000-8000-00000000000a' $$,
    '42501',
    null,
    'an authenticated user cannot change their role'
);
select throws_ok(
    $$ insert into public.profiles (id) values ('00000000-0000-4000-8000-00000000000c') $$,
    '42501',
    null,
    'an authenticated user cannot insert profiles'
);
select throws_ok(
    $$ delete from public.profiles where id = '00000000-0000-4000-8000-00000000000a' $$,
    '42501',
    null,
    'an authenticated user cannot delete profiles'
);

-- anon --------------------------------------------------------------------
reset role;
set local role anon;
select throws_ok(
    $$ select count(*) from public.profiles $$,
    '42501',
    null,
    'anon cannot read profiles'
);

-- Verify updates as the table owner --------------------------------------
reset role;
select is(
    (select display_name from public.profiles where id = '00000000-0000-4000-8000-00000000000a'),
    'Renamed A',
    'an authenticated user can update their own display_name'
);
select is(
    (select display_name from public.profiles where id = '00000000-0000-4000-8000-00000000000b'),
    null,
    'an authenticated user cannot update another user''s profile'
);

select * from finish();
rollback;
