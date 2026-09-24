import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';

import { readSupabasePublicEnv } from './env';

/**
 * Supabase client for Server Components, Server Actions and Route Handlers.
 * Create one per request; never share it between requests.
 */
export async function createSupabaseServerClient() {
  // Read cookies first: it marks the route as request-time rendered, so no
  // session-dependent page is ever prerendered at build time.
  const cookieStore = await cookies();
  const { url, anonKey } = readSupabasePublicEnv();

  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Components cannot set cookies. The proxy refreshes the
          // session on every request, so this is safe to ignore there.
        }
      },
    },
  });
}
