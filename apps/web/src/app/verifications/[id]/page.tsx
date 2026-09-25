import type { VerificationDetailResponse } from '@skillmirror/contracts';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { abandonVerification, startVerification, submitVerification } from '@/app/verifications/actions';
import { AutoRefresh } from '@/components/auto-refresh';
import { AppHeader } from '@/components/experience/app-header';
import { ErrorNotice } from '@/components/experience/states';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ChallengeForm } from '@/components/verification/challenge-form';
import { ResultPanel, StatusBadge } from '@/components/verification/session-card';
import { ApiError, apiRequest } from '@/lib/api';
import { isUuid } from '@/lib/course-selection';
import { requireApiSession } from '@/lib/session';
import { ASSESSMENT_LABEL, STATUS, minutesLabel, responseText, triggerText } from '@/lib/verification';

export default async function VerificationPage({ params }: PageProps<'/verifications/[id]'>) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const { accessToken } = await requireApiSession(`/verifications/${id}`);

  let detail: VerificationDetailResponse;
  try {
    detail = await apiRequest<VerificationDetailResponse>(`/v1/verifications/${id}`, accessToken);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    if (!(error instanceof ApiError)) throw error;
    return (
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-10 sm:px-6">
        <AppHeader current="/verifications" title="Verification" />
        <ErrorNotice title="This check could not be loaded" message={error.message} />
      </main>
    );
  }
  const { session, challenge } = detail;
  const waiting = session.status === 'PREPARING' || session.status === 'EVALUATING';

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-10 sm:px-6" data-testid="verification-page" data-state={session.state}>
      <AppHeader current="/verifications" title={`Check: ${session.canonical_name}`} description={triggerText(session)}>
        <StatusBadge session={session} />
      </AppHeader>
      {waiting ? <AutoRefresh intervalMs={3000} /> : null}
      <p className="flex flex-wrap gap-x-3 text-sm text-muted-foreground">
        {session.assessment_type ? <span>{ASSESSMENT_LABEL[session.assessment_type]}</span> : null}
        {session.estimated_minutes ? <span>{minutesLabel(session.estimated_minutes)}</span> : null}
        <Link href={`/skills/${session.skill_id}`} className="underline underline-offset-4">
          Skill details
        </Link>
        <Link href="/verifications" className="underline underline-offset-4">
          Verification Center
        </Link>
      </p>

      {session.state === 'READY' ? (
        <Card>
          <CardHeader>
            <CardTitle>Ready when you are</CardTitle>
            <CardDescription>
              One short challenge about {session.canonical_name}. Work on your own, without AI help: the point is to
              show what you can do independently. You can leave and come back; the check is kept.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form action={startVerification}>
              <input type="hidden" name="session_id" value={session.id} />
              <Button type="submit" data-testid="verification-start">
                Start the check
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {challenge ? (
        <Card>
          <CardHeader>
            <CardTitle>Challenge</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="whitespace-pre-wrap text-base" data-testid="challenge-prompt">
              {challenge.prompt}
            </p>
            {session.state === 'IN_PROGRESS' ? (
              <ChallengeForm challenge={challenge} submit={submitVerification} />
            ) : (
              <div className="space-y-1 text-sm">
                {challenge.choices.length > 0 ? (
                  <ul className="space-y-1 text-muted-foreground">
                    {challenge.choices.map((c) => (
                      <li key={c.key}>
                        <span className="font-medium">{c.key}.</span> {c.text}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p data-testid="submitted-answer">
                  <span className="font-medium">Your answer: </span>
                  <span className="whitespace-pre-wrap">{responseText(detail.response)}</span>
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}

      {session.status !== 'READY' && session.status !== 'IN_PROGRESS' && !session.result ? (
        <p className="text-sm text-muted-foreground" data-testid="verification-status-detail">
          {STATUS[session.status].detail}
        </p>
      ) : null}

      {session.result ? (
        <Card>
          <CardHeader>
            <CardTitle>Result</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ResultPanel result={session.result} />
            <Link href={`/skills/${session.skill_id}`} className="text-sm underline underline-offset-4" data-testid="result-skill-link">
              See how this changed {session.canonical_name}
            </Link>
          </CardContent>
        </Card>
      ) : null}

      {session.state === 'IN_PROGRESS' ? (
        <form action={abandonVerification} className="text-xs text-muted-foreground">
          <input type="hidden" name="session_id" value={session.id} />
          Not the right moment?{' '}
          <button type="submit" className="underline underline-offset-4" data-testid="verification-abandon">
            Stop this check
          </button>{' '}
          (a stopped check never counts against you).
        </form>
      ) : null}
    </main>
  );
}
