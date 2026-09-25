import type {
  Course,
  CourseListResponse,
  LedgerResponse,
  MeResponse,
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
import { AttentionPanel, KnowledgePanel, NextStepsPanel } from '@/components/experience/dashboard-sections';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { COURSE_COOKIE, resolveSelectedCourse } from '@/lib/course-selection';
import { graphStatusLabel } from '@/lib/courses';
import { knowledgeSummary, splitRecommendations } from '@/lib/dashboard';
import { MASTERY, countStates, type StateCounts } from '@/lib/experience';
import { areaLinks } from '@/lib/roles';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { cn } from '@/lib/utils';

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
      ? (await apiRequest<RecommendationsResponse>(`/v1/recommendations?course_id=${selected}&limit=6`, accessToken))
          .recommendations
      : [];
    return { state: 'ok', courses: courses.courses, ledger, selected, recommendations };
  } catch (error) {
    if (error instanceof ApiError) return { state: 'error', message: error.message };
    throw error;
  }
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

  const [learning, me] = await Promise.all([
    loadLearning(session?.access_token, cookieValue, query),
    // Only decides which teacher / admin links to show; the backend re-checks the role.
    session?.access_token
      ? apiRequest<MeResponse>('/v1/me', session.access_token).catch(() => null)
      : Promise.resolve(null),
  ]);
  const selectedCourse = learning.state === 'ok' ? learning.courses.find((c) => c.id === learning.selected) : undefined;
  const { attention, next } = splitRecommendations(learning.state === 'ok' ? learning.recommendations : []);

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
      <AppHeader
        current="/dashboard"
        areas={areaLinks(me)}
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
            <KnowledgePanel
              courseName={selectedCourse.name}
              summary={knowledgeSummary(learning.ledger.skills, selectedCourse.id)}
            />
          ) : null}

          <AttentionPanel recommendations={attention} />
          <NextStepsPanel recommendations={next} />
        </>
      )}

      <nav aria-label="More" className="flex flex-wrap gap-x-4 gap-y-2 border-t pt-4 text-sm text-muted-foreground">
        <Link href="/activity" className="underline underline-offset-4" data-testid="activity-link">
          Activity
        </Link>
        <Link href="/verifications" className="underline underline-offset-4" data-testid="verifications-link">
          Verification Center
        </Link>
        <Link href="/courses" className="underline underline-offset-4" data-testid="courses-link">
          Courses
        </Link>
        <Link href="/settings" className="underline underline-offset-4" data-testid="settings-link">
          Settings &amp; diagnostics
        </Link>
      </nav>
    </main>
  );
}
