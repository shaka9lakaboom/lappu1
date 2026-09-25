import type {
  Course,
  CourseListResponse,
  HealthResponse,
  LedgerResponse,
  Profile,
  Recommendation,
  RecommendationsResponse,
} from '@skillmirror/contracts';
import { cookies } from 'next/headers';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { signOut } from '@/app/auth/actions';
import { selectCourse } from '@/app/courses/actions';
import { AppHeader } from '@/components/experience/app-header';
import { CourseSelector } from '@/components/experience/course-selector';
import { RecommendationCard } from '@/components/experience/panels';
import { EmptyState, ErrorNotice, StateCountTiles } from '@/components/experience/states';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { COURSE_COOKIE, resolveSelectedCourse } from '@/lib/course-selection';
import { graphStatusLabel } from '@/lib/courses';
import { MASTERY, countStates, type StateCounts } from '@/lib/experience';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { cn } from '@/lib/utils';

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

type Learning =
  | { state: 'ok'; courses: Course[]; ledger: LedgerResponse; selected: string | null; recommendations: Recommendation[] }
  | { state: 'error'; message: string };

async function loadLearning(
  accessToken: string | undefined,
  cookieValue: string | undefined,
  query: string | string[] | undefined,
): Promise<Learning> {
  if (!accessToken) return { state: 'error', message: 'Your session has no access token; sign in again.' };
  try {
    const [courses, ledger] = await Promise.all([
      apiRequest<CourseListResponse>('/v1/courses', accessToken),
      apiRequest<LedgerResponse>('/v1/ledger', accessToken),
    ]);
    const selected = resolveSelectedCourse(courses.courses, cookieValue, query);
    const recommendations = selected
      ? (await apiRequest<RecommendationsResponse>(`/v1/recommendations?course_id=${selected}&limit=4`, accessToken))
          .recommendations
      : [];
    return { state: 'ok', courses: courses.courses, ledger, selected, recommendations };
  } catch (error) {
    if (error instanceof ApiError) return { state: 'error', message: error.message };
    throw error;
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

function CourseCard({ course, counts, selected }: { course: Course; counts: StateCounts; selected: boolean }) {
  return (
    <Link href={`/skills?course=${course.id}`} data-testid="dashboard-course-card" data-course-id={course.id}>
      <Card className={cn('h-full transition-colors hover:bg-muted/40', selected && 'ring-2 ring-ring')}>
        <CardHeader>
          <CardTitle className="text-base">{course.name}</CardTitle>
          <CardDescription>{[course.level, course.subject].filter(Boolean).join(' · ') || 'Course'}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          {course.graph_status === 'READY' ? (
            <p className="text-muted-foreground">
              {counts.DEMONSTRATED + counts.VERIFIED} demonstrated · {counts.DEVELOPING} developing · {counts.EMERGING}{' '}
              emerging · {counts.UNKNOWN} {MASTERY.UNKNOWN.label.toLowerCase()}
            </p>
          ) : (
            <p className="text-muted-foreground">{graphStatusLabel(course)}</p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}

export default async function DashboardPage({ searchParams }: PageProps<'/dashboard'>) {
  const supabase = await createSupabaseServerClient();
  // getUser() re-validates the session with the Supabase Auth server.
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in?next=/dashboard');
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const query = (await searchParams).course;
  const cookieValue = (await cookies()).get(COURSE_COOKIE)?.value;

  const [{ data: profile, error: profileError }, apiStatus, learning] = await Promise.all([
    supabase
      .from('profiles')
      .select('id, role, display_name, timezone, created_at, updated_at')
      .eq('id', user.id)
      .maybeSingle<Profile>(),
    fetchApiStatus(),
    loadLearning(session?.access_token, cookieValue, query),
  ]);
  const selectedCourse = learning.state === 'ok' ? learning.courses.find((c) => c.id === learning.selected) : undefined;

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
      <AppHeader
        current="/dashboard"
        title="Dashboard"
        description={
          <>
            Signed in as <span data-testid="user-email">{user.email}</span>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          {learning.state === 'ok' ? (
            <CourseSelector courses={learning.courses} selected={learning.selected} returnTo="/dashboard" action={selectCourse} />
          ) : null}
          <form action={signOut}>
            <Button type="submit" variant="outline">
              Sign out
            </Button>
          </form>
        </div>
      </AppHeader>

      {learning.state === 'error' ? (
        <ErrorNotice title="Your skills could not be loaded" message={learning.message} />
      ) : learning.courses.length === 0 ? (
        <EmptyState title="Add your first course" testId="dashboard-no-courses">
          SkillMirror maps your AI-assisted work to the skills of your courses.{' '}
          <Link href="/courses/new" className="underline underline-offset-4">
            Create a course
          </Link>{' '}
          to start.
        </EmptyState>
      ) : (
        <>
          <section className="space-y-3" aria-labelledby="courses-heading">
            <h2 id="courses-heading" className="text-lg font-semibold">
              Your courses
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="dashboard-courses">
              {learning.courses.map((course) => (
                <CourseCard
                  key={course.id}
                  course={course}
                  counts={countStates(learning.ledger.skills, course.id)}
                  selected={course.id === learning.selected}
                />
              ))}
            </div>
          </section>

          {selectedCourse ? (
            <section className="space-y-3" aria-labelledby="states-heading">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 id="states-heading" className="text-lg font-semibold">
                  Skills in {selectedCourse.name}
                </h2>
                <Link href="/skills" className="text-sm underline underline-offset-4" data-testid="skill-map-link">
                  Open the skill map
                </Link>
              </div>
              <StateCountTiles counts={countStates(learning.ledger.skills, selectedCourse.id)} />
              <p className="text-xs text-muted-foreground">
                “{MASTERY.UNKNOWN.label}” means SkillMirror has not seen enough of your own work on a skill to form a view.
                It is not a low score.
              </p>
            </section>
          ) : null}

          <section className="space-y-3" aria-labelledby="next-heading">
            <h2 id="next-heading" className="text-lg font-semibold">
              Suggested next steps
            </h2>
            {learning.recommendations.length === 0 ? (
              <EmptyState title="Nothing to do yet" testId="recommendations-empty">
                Keep working as usual. Suggestions appear once your activity shows something worth practising or
                checking.
              </EmptyState>
            ) : (
              <Card>
                <CardContent className="divide-y pt-6" data-testid="dashboard-recommendations">
                  {learning.recommendations.map((rec) => (
                    <div key={rec.id} className="py-3 first:pt-0 last:pb-0">
                      <RecommendationCard rec={rec} />
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </section>
        </>
      )}

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

        <div className="space-y-4">
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
            <CardContent className="flex flex-wrap gap-4 pt-6 text-sm">
              <Link href="/courses" className="underline underline-offset-4" data-testid="courses-link">
                Open Courses
              </Link>
              <Link href="/activity" className="underline underline-offset-4" data-testid="activity-link">
                Open Activity
              </Link>
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
