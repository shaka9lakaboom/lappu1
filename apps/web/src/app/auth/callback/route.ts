import type { EmailOtpType } from '@supabase/supabase-js';
import { NextResponse, type NextRequest } from 'next/server';

import { safeRedirectPath } from '@/lib/redirect';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * Target of Supabase email links (signup confirmation). Supports both the
 * PKCE `?code=` redirect and the `?token_hash=&type=` template variant.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const next = safeRedirectPath(searchParams.get('next'));
  const code = searchParams.get('code');
  const tokenHash = searchParams.get('token_hash');
  const type = searchParams.get('type') as EmailOtpType | null;

  const supabase = await createSupabaseServerClient();
  let failed = true;
  if (code) {
    failed = Boolean((await supabase.auth.exchangeCodeForSession(code)).error);
  } else if (tokenHash && type) {
    failed = Boolean((await supabase.auth.verifyOtp({ token_hash: tokenHash, type })).error);
  }

  const target = request.nextUrl.clone();
  target.search = '';
  if (failed) {
    target.pathname = '/sign-in';
    target.searchParams.set('error', 'The confirmation link is invalid or has expired. Please sign in or sign up again.');
  } else {
    target.pathname = next;
  }
  return NextResponse.redirect(target);
}
