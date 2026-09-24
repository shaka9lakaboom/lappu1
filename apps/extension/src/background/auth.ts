/**
 * The learner's Supabase Auth session, held by the service worker.
 *
 * The learner signs in with their SkillMirror (Supabase) account in the
 * popup; the worker calls Supabase Auth's public token endpoints with the anon
 * key, exactly as the web app does. The session lives in the extension's own
 * IndexedDB (unreadable by web pages and content scripts) and is refreshed
 * before it expires. The access token is sent to the backend as a Bearer
 * token; the backend verifies it and derives the learner from it.
 */

export interface StoredSession {
  access_token: string;
  refresh_token: string;
  /** Unix seconds. */
  expires_at: number;
  user: { id: string; email: string | null };
}

export interface SessionStore {
  getValue<T>(key: string): Promise<T | undefined>;
  setValue(key: string, value: unknown): Promise<void>;
}

export class AuthError extends Error {
  constructor(
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

const SESSION_KEY = 'auth.session';
const REFRESH_MARGIN_SECONDS = 60;

interface TokenResponse {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  expires_at?: number;
  user?: { id?: string; email?: string | null };
  error_description?: string;
  msg?: string;
  error?: string;
}

export class SupabaseAuthClient {
  private refreshing: Promise<StoredSession | null> | null = null;

  constructor(
    private readonly supabaseUrl: string,
    private readonly anonKey: string,
    private readonly store: SessionStore,
    private readonly fetchImpl: typeof fetch = (...args) => fetch(...args),
    private readonly nowSeconds: () => number = () => Math.floor(Date.now() / 1000),
  ) {}

  private async token(grant: 'password' | 'refresh_token', body: Record<string, string>): Promise<StoredSession> {
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.supabaseUrl}/auth/v1/token?grant_type=${grant}`, {
        method: 'POST',
        headers: { apikey: this.anonKey, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AuthError('Could not reach Supabase Auth.');
    }
    const data = (await response.json().catch(() => ({}))) as TokenResponse;
    if (!response.ok || !data.access_token || !data.refresh_token || !data.user?.id) {
      throw new AuthError(data.error_description ?? data.msg ?? data.error ?? `Sign-in failed (HTTP ${response.status}).`, response.status);
    }
    const session: StoredSession = {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      expires_at: data.expires_at ?? this.nowSeconds() + (data.expires_in ?? 3600),
      user: { id: data.user.id, email: data.user.email ?? null },
    };
    await this.store.setValue(SESSION_KEY, session);
    return session;
  }

  signInWithPassword(email: string, password: string): Promise<StoredSession> {
    return this.token('password', { email, password });
  }

  async signOut(): Promise<void> {
    const session = await this.store.getValue<StoredSession>(SESSION_KEY);
    await this.store.setValue(SESSION_KEY, undefined);
    if (session) {
      await this.fetchImpl(`${this.supabaseUrl}/auth/v1/logout`, {
        method: 'POST',
        headers: { apikey: this.anonKey, Authorization: `Bearer ${session.access_token}` },
      }).catch(() => undefined);
    }
  }

  /** The stored session without refreshing (for display). */
  current(): Promise<StoredSession | undefined> {
    return this.store.getValue<StoredSession>(SESSION_KEY);
  }

  /** A session whose access token is valid for at least a minute, or null when signed out. */
  async getSession(): Promise<StoredSession | null> {
    const session = await this.current();
    if (!session) return null;
    if (session.expires_at - this.nowSeconds() > REFRESH_MARGIN_SECONDS) return session;
    return this.refresh();
  }

  /** Refreshes once even if called concurrently (refresh tokens rotate). */
  refresh(): Promise<StoredSession | null> {
    this.refreshing ??= (async () => {
      try {
        const session = await this.current();
        if (!session) return null;
        try {
          return await this.token('refresh_token', { refresh_token: session.refresh_token });
        } catch (error) {
          // A rejected refresh token means the session is over; a network error does not.
          if (error instanceof AuthError && error.status !== null && error.status >= 400 && error.status < 500) {
            await this.store.setValue(SESSION_KEY, undefined);
            return null;
          }
          throw error;
        }
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  }
}
