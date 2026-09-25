import { SKILL_CANDIDATE_STATUSES, type AdminSkillCandidatesResponse, type SkillCandidateStatus } from '@skillmirror/contracts';
import Link from 'next/link';

import { reviewCandidate } from '@/app/admin/actions';
import { ActionForm } from '@/components/areas/action-form';
import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { formKey } from '@/lib/admin';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';
import { cn } from '@/lib/utils';

export default async function AdminSkillCandidatesPage({ searchParams }: PageProps<'/admin/skill-candidates'>) {
  const requested = (await searchParams).status;
  const status: SkillCandidateStatus | 'ALL' =
    requested === 'ALL' ? 'ALL' : (SKILL_CANDIDATE_STATUSES.find((s) => s === requested) ?? 'PENDING_REVIEW');
  const { accessToken } = await requireApiSession('/admin/skill-candidates');
  const loaded = await loadArea<AdminSkillCandidatesResponse>(`/v1/admin/skill-candidates?status=${status}`, accessToken);

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
      <AreaHeader
        area="admin"
        current="/admin/skill-candidates"
        title="Skill candidates"
        description="Names the mapper proposed that are not in the registry. Nothing becomes a skill without a review."
      />
      {loaded.state === 'forbidden' ? (
        <ForbiddenPanel area="admin" />
      ) : loaded.state !== 'ok' ? (
        <ErrorNotice title="Candidates could not be loaded" message={loaded.state === 'error' ? loaded.message : 'Not found.'} />
      ) : (
        <>
          <nav className="flex flex-wrap gap-1 text-sm" aria-label="Candidate status">
            {(['PENDING_REVIEW', 'APPROVED', 'MERGED', 'REJECTED', 'ALL'] as const).map((s) => (
              <Link
                key={s}
                href={`/admin/skill-candidates?status=${s}`}
                aria-current={status === s ? 'page' : undefined}
                className={cn('rounded-md px-3 py-1.5', status === s ? 'bg-muted font-medium' : 'text-muted-foreground')}
              >
                {s === 'ALL' ? 'All' : s.replace('_', ' ').toLowerCase()}
                {s !== 'ALL' ? ` (${loaded.data.counts[s] ?? 0})` : ''}
              </Link>
            ))}
          </nav>
          {loaded.data.candidates.length === 0 ? (
            <EmptyState title="No candidate here" testId="candidates-empty" />
          ) : (
            <div className="space-y-4" data-testid="candidates">
              {loaded.data.candidates.map((c) => (
                <Card key={c.id} data-testid="candidate-card" data-candidate-id={c.id} data-status={c.status}>
                  <CardHeader>
                    <CardTitle className="text-base">{c.canonical_name}</CardTitle>
                    <CardDescription>
                      {c.status} · proposed {c.occurrences} {c.occurrences === 1 ? 'time' : 'times'}
                      {c.first_course ? ` · first in ${c.first_course.name}` : ''}
                      {c.parent ? ` · under ${c.parent.name}` : ''} · {formatTime(c.created_at)}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    {c.description ? <p>{c.description}</p> : <p className="text-muted-foreground">No description.</p>}
                    {c.resolved_skill ? (
                      <p className="text-muted-foreground">
                        Resolved to <span className="font-medium text-foreground">{c.resolved_skill.name}</span>
                        {c.review_note ? ` · “${c.review_note}”` : ''}
                      </p>
                    ) : null}
                    {c.status === 'PENDING_REVIEW' ? (
                      <>
                        {c.similar_skills.length ? (
                          <p className="text-muted-foreground">
                            Similar registry skills: {c.similar_skills.map((s) => s.name).join(' · ')}
                          </p>
                        ) : null}
                        <ActionForm
                          action={reviewCandidate}
                          hidden={{ candidate_id: c.id, action: 'APPROVE', key: formKey('approve') }}
                          submitLabel="Approve as a new skill"
                          testId="approve-form"
                          variant="default"
                        >
                          {!c.description ? (
                            <Input name="description" placeholder="Description (8-600 characters)" aria-label="Description" required minLength={8} maxLength={600} />
                          ) : null}
                          <select name="difficulty_band" aria-label="Difficulty band" defaultValue="" className="h-8 rounded-md border bg-background px-2 text-xs">
                            <option value="">Difficulty band (optional)</option>
                            {[1, 2, 3, 4, 5].map((b) => (
                              <option key={b} value={b}>
                                Band {b}
                              </option>
                            ))}
                          </select>
                        </ActionForm>
                        <ActionForm
                          action={reviewCandidate}
                          hidden={{ candidate_id: c.id, action: 'MERGE', key: formKey('merge') }}
                          submitLabel="Merge as an alias"
                          testId="merge-form"
                        >
                          {c.similar_skills.length ? (
                            <select name="target_skill_id" aria-label="Merge into" className="h-8 rounded-md border bg-background px-2 text-xs">
                              {c.similar_skills.map((s) => (
                                <option key={s.id} value={s.id}>
                                  {s.name}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <Input name="target_skill_id" placeholder="Target skill id" aria-label="Target skill id" className="h-8 w-80 text-xs" required />
                          )}
                        </ActionForm>
                        <ActionForm
                          action={reviewCandidate}
                          hidden={{ candidate_id: c.id, action: 'REJECT', key: formKey('reject') }}
                          submitLabel="Reject"
                          testId="reject-form"
                          variant="ghost"
                        >
                          <Input name="note" placeholder="Note (optional)" aria-label="Note" className="h-8 w-64 text-xs" maxLength={1000} />
                        </ActionForm>
                      </>
                    ) : null}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </main>
  );
}
