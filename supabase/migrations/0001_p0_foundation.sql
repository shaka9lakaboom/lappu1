-- 0001_p0_foundation.sql
-- SkillMirror P0 foundation: pgvector, application roles, profiles, RLS,
-- and automatic profile creation on Supabase Auth signup.
--
-- All later schema changes must be new, higher-numbered migrations.

-- pgvector lives in the same database as the system of record (architecture §7).
create extension if not exists vector with schema extensions;

-- Application role. New accounts are always STUDENT (architecture §4, step 1);
-- elevation to TEACHER/ADMIN is an operator action using the service role.
create type public.app_role as enum ('STUDENT', 'TEACHER', 'ADMIN');

create table public.profiles (
    id           uuid primary key references auth.users (id) on delete cascade,
    role         public.app_role not null default 'STUDENT',
    display_name text check (display_name is null or char_length(display_name) between 1 and 120),
    timezone     text not null default 'UTC' check (char_length(timezone) between 1 and 64),
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

comment on table public.profiles is 'Application profile, one row per auth.users identity.';
comment on column public.profiles.role is 'Authorization role. Not writable by clients.';

-- ---------------------------------------------------------------------------
-- Row Level Security and privileges
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;

-- Supabase grants broad default privileges on public tables; narrow them.
-- Clients may read their own row and change only display_name / timezone.
-- Rows are created by the signup trigger, never by clients.
revoke all on table public.profiles from anon, authenticated;
grant select on table public.profiles to authenticated;
grant update (display_name, timezone) on table public.profiles to authenticated;

create policy profiles_select_own
    on public.profiles
    for select
    to authenticated
    using ((select auth.uid()) = id);

create policy profiles_update_own
    on public.profiles
    for update
    to authenticated
    using ((select auth.uid()) = id)
    with check ((select auth.uid()) = id);

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create trigger profiles_set_updated_at
    before update on public.profiles
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Profile creation on signup
-- ---------------------------------------------------------------------------
-- SECURITY DEFINER so the Auth service can insert into public.profiles.
-- The role is deliberately NOT read from raw_user_meta_data: that field is
-- supplied by the signing-up client and must never grant privileges.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, display_name)
    values (
        new.id,
        nullif(btrim(left(new.raw_user_meta_data ->> 'display_name', 120)), '')
    );
    return new;
end;
$$;

revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();
