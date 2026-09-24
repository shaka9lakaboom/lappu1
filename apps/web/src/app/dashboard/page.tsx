import type { HealthResponse, Profile } from '@skillmirror/contracts';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { signOut } from '@/app/auth/actions';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { createSupabaseServerClient } from '@/lib/supabase/server';

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

export default async function DashboardPage() {
  const supabase = await createSupabaseServerClient();
  // getUser() re-validates the session with the Supabase Auth server.
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in?next=/dashboard');

  const [{ data: profile, error: profileError }, apiStatus] = await Promise.all([
    supabase
      .from('profiles')
      .select('id, role, display_name, timezone, created_at, updated_at')
      .eq('id', user.id)
      .maybeSingle<Profile>(),
    fetchApiStatus(),
  ]);

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-12">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Signed in as <span data-testid="user-email">{user.email}</span>
          </p>
        </div>
        <form action={signOut}>
          <Button type="submit" variant="outline">
            Sign out
          </Button>
        </form>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>
            Identity verified by Supabase Auth; profile read through row-level security.
          </CardDescription>
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
          <CardTitle>Backend API</CardTitle>
          <CardDescription>Live result of GET /health on NEXT_PUBLIC_API_URL.</CardDescription>
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

      <Card>
        <CardHeader>
          <CardTitle>Captured activity</CardTitle>
          <CardDescription>
            Messages the SkillMirror Companion captured from ChatGPT, with their sync and processing status.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          <Link href="/activity" className="underline underline-offset-4" data-testid="activity-link">
            Open Activity
          </Link>
        </CardContent>
      </Card>

      <p className="text-sm text-muted-foreground">
        Courses, skills and evidence will appear here once processing exists. Nothing is shown until there is
        real evidence.
      </p>
    </main>
  );
}
