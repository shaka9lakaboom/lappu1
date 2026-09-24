import type { User } from '@supabase/supabase-js';
import { redirect } from 'next/navigation';

import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * The verified user plus their access token for backend API calls.
 * getUser() re-validates the session with Supabase Auth before the token is used.
 */
export async function requireApiSession(next: string): Promise<{ user: User; accessToken: string }> {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect(`/sign-in?next=${encodeURIComponent(next)}`);
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) redirect(`/sign-in?next=${encodeURIComponent(next)}`);
  return { user, accessToken: session.access_token };
}
