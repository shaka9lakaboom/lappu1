/**
 * Validated public Supabase configuration.
 *
 * There is deliberately no placeholder fallback: a missing or unsafe value
 * throws with a message naming the variable, so misconfiguration is visible
 * immediately instead of surfacing later as confusing auth failures.
 */

export interface SupabasePublicEnv {
  url: string;
  anonKey: string;
}

export class SupabaseEnvError extends Error {
  constructor(message: string) {
    super(`${message} Copy apps/web/.env.example to apps/web/.env.local and fill in the values from your Supabase project.`);
    this.name = 'SupabaseEnvError';
  }
}

interface RawEnv {
  NEXT_PUBLIC_SUPABASE_URL?: string;
  NEXT_PUBLIC_SUPABASE_ANON_KEY?: string;
}

// Literal `process.env.NEXT_PUBLIC_*` reads are required for Next.js to
// inline these values at build time.
const processEnv = (): RawEnv => ({
  NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
});

function jwtRole(key: string): string | undefined {
  const parts = key.split('.');
  if (parts.length !== 3) return undefined;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as { role?: unknown };
    return typeof payload.role === 'string' ? payload.role : undefined;
  } catch {
    return undefined;
  }
}

export function readSupabasePublicEnv(env: RawEnv = processEnv()): SupabasePublicEnv {
  const url = env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const anonKey = env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();

  const missing = [
    !url && 'NEXT_PUBLIC_SUPABASE_URL',
    !anonKey && 'NEXT_PUBLIC_SUPABASE_ANON_KEY',
  ].filter(Boolean);
  if (missing.length > 0 || !url || !anonKey) {
    throw new SupabaseEnvError(`Missing required Supabase environment variable(s): ${missing.join(', ')}.`);
  }

  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new SupabaseEnvError(`NEXT_PUBLIC_SUPABASE_URL is not a valid URL: "${url}".`);
  }
  const isLocal = parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1';
  if (parsed.protocol !== 'https:' && !(isLocal && parsed.protocol === 'http:')) {
    throw new SupabaseEnvError('NEXT_PUBLIC_SUPABASE_URL must use https (http is allowed only for localhost).');
  }

  // A secret key in a NEXT_PUBLIC_ variable would be shipped to every browser.
  if (anonKey.startsWith('sb_secret_') || jwtRole(anonKey) === 'service_role') {
    throw new SupabaseEnvError(
      'NEXT_PUBLIC_SUPABASE_ANON_KEY contains a secret/service-role key. Use the anon or publishable key instead.',
    );
  }

  return { url: parsed.origin, anonKey };
}
