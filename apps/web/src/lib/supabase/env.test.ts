import { describe, expect, it } from 'vitest';

import { readSupabasePublicEnv, SupabaseEnvError } from './env';

const VALID = {
  NEXT_PUBLIC_SUPABASE_URL: 'https://project-ref.supabase.co',
  NEXT_PUBLIC_SUPABASE_ANON_KEY: 'sb_publishable_abc123',
};

function fakeJwt(payload: object): string {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(payload)}.signature`;
}

describe('readSupabasePublicEnv', () => {
  it('returns validated values', () => {
    expect(readSupabasePublicEnv(VALID)).toEqual({
      url: 'https://project-ref.supabase.co',
      anonKey: 'sb_publishable_abc123',
    });
  });

  it('names every missing variable', () => {
    expect(() => readSupabasePublicEnv({})).toThrow(
      /NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY/,
    );
  });

  it('treats blank values as missing', () => {
    expect(() => readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_ANON_KEY: '   ' })).toThrow(
      /Missing required Supabase environment variable\(s\): NEXT_PUBLIC_SUPABASE_ANON_KEY\./,
    );
  });

  it('throws a SupabaseEnvError with setup guidance', () => {
    expect(() => readSupabasePublicEnv({})).toThrow(SupabaseEnvError);
    expect(() => readSupabasePublicEnv({})).toThrow(/apps\/web\/\.env\.local/);
  });

  it('rejects an invalid URL', () => {
    expect(() => readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_URL: 'not a url' })).toThrow(
      /not a valid URL/,
    );
  });

  it('rejects plain http for non-local hosts', () => {
    expect(() =>
      readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_URL: 'http://project-ref.supabase.co' }),
    ).toThrow(/must use https/);
  });

  it('allows http for a local Supabase stack', () => {
    expect(readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_URL: 'http://127.0.0.1:54321' }).url).toBe(
      'http://127.0.0.1:54321',
    );
  });

  it('refuses a secret key', () => {
    expect(() => readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_ANON_KEY: 'sb_secret_xyz' })).toThrow(
      /secret\/service-role key/,
    );
  });

  it('refuses a legacy service_role JWT', () => {
    expect(() =>
      readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_ANON_KEY: fakeJwt({ role: 'service_role' }) }),
    ).toThrow(/secret\/service-role key/);
  });

  it('accepts a legacy anon JWT', () => {
    const anon = fakeJwt({ role: 'anon' });
    expect(readSupabasePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_ANON_KEY: anon }).anonKey).toBe(anon);
  });
});
