import type { Recommendation } from '@skillmirror/contracts';
import Link from 'next/link';

import { RecommendationCard } from '@/components/experience/panels';
import { EmptyState, StateCountTiles } from '@/components/experience/states';
import { Card, CardContent } from '@/components/ui/card';
import { knowledgeSentence, type KnowledgeSummary } from '@/lib/dashboard';
import { MASTERY } from '@/lib/experience';

/** "What does SkillMirror know?" and "What is still unknown?" for the selected course. */
export function KnowledgePanel({ courseName, summary }: { courseName: string; summary: KnowledgeSummary }) {
  return (
    <section className="space-y-3" aria-labelledby="states-heading" data-testid="dashboard-knowledge">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="states-heading" className="text-lg font-semibold">
          What SkillMirror knows about {courseName}
        </h2>
        <Link href="/skills" className="text-sm underline underline-offset-4" data-testid="skill-map-link">
          Open the skill map
        </Link>
      </div>
      <p className="text-sm" data-testid="knowledge-sentence">
        {knowledgeSentence(summary)}
      </p>
      <StateCountTiles counts={summary.counts} />
      <p className="text-xs text-muted-foreground">
        “{MASTERY.UNKNOWN.label}” means SkillMirror has not seen enough of your own work on a skill to form a view. It is
        not a low score.
      </p>
    </section>
  );
}

function RecommendationList({ recommendations, testId }: { recommendations: Recommendation[]; testId: string }) {
  return (
    <Card>
      <CardContent className="divide-y pt-6" data-testid={testId}>
        {recommendations.map((rec) => (
          <div key={rec.id} className="py-3 first:pt-0 last:pb-0">
            <RecommendationCard rec={rec} />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/** "What needs my attention?": the current checks (VERIFY / REVERIFY) only. */
export function AttentionPanel({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <section className="space-y-3" aria-labelledby="attention-heading" data-testid="dashboard-attention">
      <h2 id="attention-heading" className="text-lg font-semibold">
        Needs your attention
      </h2>
      {recommendations.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="attention-empty">
          Nothing needs your attention right now. SkillMirror suggests a short check only when the AI has repeatedly
          done an important skill for you.
        </p>
      ) : (
        <RecommendationList recommendations={recommendations} testId="dashboard-attention-list" />
      )}
    </section>
  );
}

/** "What should I do next?": practice and prerequisite suggestions. */
export function NextStepsPanel({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <section className="space-y-3" aria-labelledby="next-heading">
      <h2 id="next-heading" className="text-lg font-semibold">
        What to do next
      </h2>
      {recommendations.length === 0 ? (
        <EmptyState title="Nothing to do yet" testId="recommendations-empty">
          Keep working as usual. Suggestions appear once your activity shows something worth practising or checking.
        </EmptyState>
      ) : (
        <RecommendationList recommendations={recommendations} testId="dashboard-recommendations" />
      )}
    </section>
  );
}
