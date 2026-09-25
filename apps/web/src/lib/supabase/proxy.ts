import { createServerClient } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';

import { readSupabasePublicEnv } from './env';

const PROTECTED_PREFIXES = ['/dashboard', '/activity', '/courses', '/skills', '/verifications', '/teacher', '/admin'];
const AUTH_PAGES = ['/sign-in', '/sign-up'];

/**
 * Refreshes the Supabase session cookie on every request and enforces
 * route-level access. Pages still verify the user themselves.
 */
export async function updateSession(request: NextRequest): Promise<NextResponse> {
  const { url, anonKey } = readSupabasePublicEnv();
  let response = NextResponse.next({ request });

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) response.cookies.set(name, value, options);
        for (const [key, value] of Object.entries(headers ?? {})) response.headers.set(key, value);
      },
    },
  });

  // Must run before any response is produced so refreshed tokens are written.
  const { data } = await supabase.auth.getClaims();
  const isAuthenticated = Boolean(data?.claims?.sub);
  const { pathname, search } = request.nextUrl;

  const redirectTo = (path: string) => {
    const target = request.nextUrl.clone();
    target.pathname = path;
    target.search = '';
    if (path === '/sign-in') target.searchParams.set('next', `${pathname}${search}`);
    const redirect = NextResponse.redirect(target);
    // Carry over any refreshed/cleared auth cookies.
    for (const cookie of response.cookies.getAll()) redirect.cookies.set(cookie);
    return redirect;
  };

  if (!isAuthenticated && PROTECTED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return redirectTo('/sign-in');
  }
  if (isAuthenticated && AUTH_PAGES.includes(pathname)) {
    return redirectTo('/dashboard');
  }

  response.headers.set('Cache-Control', 'private, no-store');
  return response;
}
