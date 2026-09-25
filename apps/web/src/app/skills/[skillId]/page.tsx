import type { SkillDetailResponse } from '@skillmirror/contracts';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { submitFeedback } from '@/app/feedback/actions';
import { AppHeader } from '@/components/experience/app-header';
import { MasteryBadge } from '@/components/experience/badges';
import { EvaluationForm } from '@/components/experience/evaluation-form';
import { EvidenceTimeline } from '@/components/experience/evidence-timeline';
import { DebtPanel, MasteryPanel, RecommendationCard } from '@/components/experience/panels';
import { ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { isUuid } from '@/lib/course-selection';
import { importanceLabel } from '@/lib/courses';
import { requireApiSession } from '@/lib/session';

export default async function SkillDetailPage({ params }: PageProps<'/skills/[skillId]'>) {
  const { skillId } = await params;
  if (!isUuid(skillId)) notFound();
  const returnTo = `/skills/${skillId}`;
  const { accessToken } = await requireApiSession(returnTo);

  let detail: SkillDetailResponse;
  try {
    detail = await apiRequest<SkillDetailResponse>(`/v1/skills/${skillId}`, accessToken);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    if (!(error instanceof ApiError)) throw error;
    return (
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6">
        <AppHeader current="/skills" title="Skill" />
        <ErrorNotice title="This skill could not be loaded" message={error.message} />
      </main>
    );
  }
  const { skill } = detail;

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6">
      <AppHeader current="/skills" title={skill.canonical_name} description={skill.description}>
        <MasteryBadge state={detail.mastery.state} />
      </AppHeader>
      <p className="flex flex-wrap gap-x-3 text-sm text-muted-foreground" data-testid="skill-context">
        {detail.courses.map((c) => (
          <span key={c.course_id}>
            {c.name}
            {c.topic_name ? ` › ${c.topic_name}` : ''} · {importanceLabel(c.importance)}
          </span>
        ))}
        {skill.difficulty_band ? <span>Difficulty {skill.difficulty_band}/5</span> : null}
        <Link href="/skills" className="underline underline-offset-4">
          Back to the skill map
        </Link>
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Mastery</CardTitle>
          </CardHeader>
          <CardContent>
            <MasteryPanel mastery={detail.mastery} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recommended action</CardTitle>
          </CardHeader>
          <CardContent data-testid="skill-recommendation">
            {detail.recommendation ? (
              <RecommendationCard rec={detail.recommendation} showSkill={false} />
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to do yet.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>AI Assistance Debt</CardTitle>
          <CardDescription>
            Whether the AI repeatedly did this skill for you without independent evidence of your own. Using AI is fine;
            this only shows where SkillMirror cannot yet tell what you can do alone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DebtPanel debt={detail.debt} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Evidence timeline</CardTitle>
          <CardDescription>
            Every piece of evidence behind this skill, with “Why?” for its exact source. Corrections exclude evidence
            without deleting it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EvidenceTimeline items={detail.evidence} returnTo={returnTo} submit={submitFeedback} />
        </CardContent>
      </Card>

      {detail.prerequisites.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Builds on</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2" data-testid="prerequisites">
              {detail.prerequisites.map((p) => (
                <li key={p.skill_id} className="flex flex-wrap items-center gap-2 text-sm">
                  <Link href={`/skills/${p.skill_id}`} className="underline underline-offset-4">
                    {p.canonical_name}
                  </Link>
                  <MasteryBadge state={p.mastery_state} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Your view</CardTitle>
        </CardHeader>
        <CardContent>
          <EvaluationForm targetType="SKILL" targetId={skill.skill_id} returnTo={returnTo} submit={submitFeedback} />
        </CardContent>
      </Card>
    </main>
  );
}
