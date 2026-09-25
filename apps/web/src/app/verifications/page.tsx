import type { VerificationSessionSummary, VerificationsResponse } from '@skillmirror/contracts';
import Link from 'next/link';

import { AutoRefresh } from '@/components/auto-refresh';
import { AppHeader } from '@/components/experience/app-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { SessionCard } from '@/components/verification/session-card';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { requireApiSession } from '@/lib/session';
import { isWaiting } from '@/lib/verification';

function Group({
  title,
  description,
  sessions,
  testId,
}: {
  title: string;
  description?: string;
  sessions: VerificationSessionSummary[];
  testId: string;
}) {
  if (sessions.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="divide-y" data-testid={testId}>
        {sessions.map((session) => (
          <SessionCard key={session.id} session={session} />
        ))}
      </CardContent>
    </Card>
  );
}

export default async function VerificationCenterPage() {
  const { accessToken } = await requireApiSession('/verifications');
  let queue: VerificationsResponse;
  try {
    queue = await apiRequest<VerificationsResponse>('/v1/verifications', accessToken);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return (
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6">
        <AppHeader current="/verifications" title="Verification Center" />
        <ErrorNotice title="Your checks could not be loaded" message={error.message} />
      </main>
    );
  }
  const open = queue.ready.length + queue.in_progress.length;
  const nothing =
    open === 0 && queue.preparing.length === 0 && queue.pending.length === 0 && queue.completed.length === 0;

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6">
      <AppHeader
        current="/verifications"
        title="Verification Center"
        description="Short, independent checks of skills the AI often did for you. A check only adds evidence; it is never a judgement of you."
      />
      {isWaiting(queue) ? <AutoRefresh intervalMs={4000} /> : null}
      <p className="text-xs text-muted-foreground" data-testid="verification-budget">
        At most {queue.budget.daily_limit} new checks are suggested per day ({queue.budget.remaining_today} left
        today).
      </p>

      {nothing ? (
        <EmptyState title="No checks right now" testId="verifications-empty">
          SkillMirror suggests a check only when the AI has repeatedly done an important skill for you and there is no
          independent evidence of your own yet. One AI question never leads to a check.{' '}
          <Link href="/dashboard" className="underline underline-offset-4">
            Back to the dashboard
          </Link>
          .
        </EmptyState>
      ) : null}

      <Group
        title="Continue"
        description="You started these; your progress is kept."
        sessions={queue.in_progress}
        testId="verifications-in-progress"
      />
      <Group
        title="Ready for you"
        description="Each takes a few minutes. Work on your own, without AI help."
        sessions={queue.ready}
        testId="verifications-ready"
      />
      <Group
        title="Being prepared"
        description="A fresh challenge is being written for these skills."
        sessions={queue.preparing}
        testId="verifications-preparing"
      />
      <Group
        title="Checking your answer"
        sessions={queue.pending}
        testId="verifications-pending"
      />
      <Group title="Completed" sessions={queue.completed} testId="verifications-completed" />
      <Group
        title="Closed without a result"
        description="Stopped, not prepared, or not gradable. None of these count against you."
        sessions={queue.closed}
        testId="verifications-closed"
      />
    </main>
  );
}
