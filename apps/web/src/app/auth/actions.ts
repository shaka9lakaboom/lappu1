'use server';

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

import { safeRedirectPath } from '@/lib/redirect';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export type AuthFormState = { error?: string; message?: string } | undefined;

const MIN_PASSWORD_LENGTH = 8;

function readCredentials(formData: FormData) {
  return {
    email: String(formData.get('email') ?? '').trim(),
    password: String(formData.get('password') ?? ''),
  };
}

async function requestOrigin(): Promise<string> {
  const h = await headers();
  const origin = h.get('origin');
  if (origin) return origin;
  const host = h.get('x-forwarded-host') ?? h.get('host');
  const proto = h.get('x-forwarded-proto') ?? 'http';
  return `${proto}://${host}`;
}

export async function signUp(_prev: AuthFormState, formData: FormData): Promise<AuthFormState> {
  const { email, password } = readCredentials(formData);
  const displayName = String(formData.get('display_name') ?? '').trim();

  if (!email || !password) return { error: 'Email and password are required.' };
  if (password.length < MIN_PASSWORD_LENGTH) {
    return { error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.` };
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: `${await requestOrigin()}/auth/callback`,
      // Read by the handle_new_user trigger. Role is never taken from here.
      data: displayName ? { display_name: displayName } : undefined,
    },
  });
  if (error) return { error: error.message };

  // With email confirmation disabled Supabase returns a session immediately.
  if (data.session) redirect('/dashboard');
  return { message: 'Check your email and follow the confirmation link, then sign in.' };
}

export async function signIn(_prev: AuthFormState, formData: FormData): Promise<AuthFormState> {
  const { email, password } = readCredentials(formData);
  if (!email || !password) return { error: 'Email and password are required.' };

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { error: error.message };

  redirect(safeRedirectPath(formData.get('next')));
}

export async function signOut(): Promise<void> {
  const supabase = await createSupabaseServerClient();
  await supabase.auth.signOut();
  redirect('/sign-in');
}
