import type { HealthResponse, MeResponse, Profile } from '@skillmirror/contracts';
import { redirect } from 'next/navigation';

import { AppHeader } from '@/components/experience/app-header';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { apiRequest } from '@/lib/api';
import { areaLinks } from '@/lib/roles';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * Settings & diagnostics (P9): the account record and the backend status. Kept off the learner
 * dashboard on purpose - ids, timestamps and version strings help troubleshooting, not learning.
 */
type ApiStatus =
  | { state: 'ok'; health: HealthResponse }
  | { state: 'unconfigured' }
  | { state: 'unreachable'; detail: string };

async function fetchApiStatus(): Promise<ApiStatus> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (!baseUrl) return { state: 'unconfigured' };
  try {
    const response = await fetch(new URL('/health', baseUrl), {
      cache: 'no-store',
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return { state: 'unreachable', detail: `HTTP ${response.status}` };
    return { state: 'ok', health: (await response.json()) as HealthResponse };
  } catch (error) {
    return { state: 'unreachable', detail: error instanceof Error ? error.message : 'request failed' };
  }
}

function Field({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="break-all font-mono text-sm" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

export default async function SettingsPage() {
  const supabase = await createSupabaseServerClient();
  // getUser() re-validates the session with the Supabase Auth server.
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in?next=/settings');
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const [{ data: profile, error: profileError }, apiStatus, me] = await Promise.all([
    supabase
      .from('profiles')
      .select('id, role, display_name, timezone, created_at, updated_at')
      .eq('id', user.id)
      .maybeSingle<Profile>(),
    fetchApiStatus(),
    session?.access_token
      ? apiRequest<MeResponse>('/v1/me', session.access_token).catch(() => null)
      : Promise.resolve(null),
  ]);

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6" data-testid="settings-page">
      <AppHeader
        current={null}
        areas={areaLinks(me)}
        title="Settings & diagnostics"
        description={
          <>
            Signed in as <span data-testid="user-email">{user.email}</span>
          </>
        }
      />
      <section className="grid gap-4 md:grid-cols-2" aria-label="Account and system">
        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
            <CardDescription>Identity verified by Supabase Auth; profile read through row-level security.</CardDescription>
          </CardHeader>
          <CardContent>
            {profileError ? (
              <p role="alert" className="text-sm text-destructive">
                Could not load profile: {profileError.message}
              </p>
            ) : !profile ? (
              <p role="alert" className="text-sm text-destructive">
                No profile row exists for this user. Has migration 0001_p0_foundation been applied?
              </p>
            ) : (
              <dl className="grid gap-4 sm:grid-cols-2">
                <Field label="User ID" value={user.id} testId="user-id" />
                <Field label="Role" value={profile.role} testId="profile-role" />
                <Field label="Display name" value={profile.display_name ?? '—'} testId="profile-display-name" />
                <Field label="Timezone" value={profile.timezone} testId="profile-timezone" />
                <Field label="Profile created" value={profile.created_at} testId="profile-created-at" />
              </dl>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Diagnostics</CardTitle>
            <CardDescription>Live result of GET /health on NEXT_PUBLIC_API_URL (for troubleshooting).</CardDescription>
          </CardHeader>
          <CardContent className="text-sm" data-testid="api-status">
            {apiStatus.state === 'ok' ? (
              <span>
                {apiStatus.health.status} · {apiStatus.health.service} {apiStatus.health.version} (
                {apiStatus.health.environment})
              </span>
            ) : apiStatus.state === 'unconfigured' ? (
              <span className="text-muted-foreground">NEXT_PUBLIC_API_URL is not set.</span>
            ) : (
              <span className="text-destructive">Unreachable: {apiStatus.detail}</span>
            )}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
