/**
 * Server-side client for the SkillMirror backend (NEXT_PUBLIC_API_URL).
 *
 * Requests carry the learner's Supabase access token as a Bearer token; the
 * backend verifies it and derives the learner from it. No secret is involved.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function apiBaseUrl(value: string | undefined = process.env.NEXT_PUBLIC_API_URL): string {
  const base = value?.trim();
  if (!base) throw new ApiError(0, 'NEXT_PUBLIC_API_URL is not set, so the SkillMirror API cannot be reached.');
  return base;
}

interface ApiRequestInit {
  method?: 'GET' | 'POST';
  body?: unknown;
  headers?: Record<string, string>;
}

export async function apiRequest<T>(
  path: string,
  accessToken: string,
  init: ApiRequestInit = {},
  fetchImpl: typeof fetch = fetch,
  baseUrl: string = apiBaseUrl(),
): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(new URL(path, baseUrl), {
      method: init.method ?? 'GET',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
  } catch (error) {
    throw new ApiError(0, `SkillMirror API unreachable: ${error instanceof Error ? error.message : 'request failed'}`);
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Non-JSON error body: keep the status text.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
