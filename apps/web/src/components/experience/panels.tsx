import type { DebtExplanation, MasteryExplanation, Recommendation } from '@skillmirror/contracts';
import Link from 'next/link';

import { DebtBadge, MasteryBadge } from '@/components/experience/badges';
import {
  DEBT_NOT_A_JUDGEMENT,
  GATE_LABELS,
  LEVEL_LABEL,
  MASTERY,
  debtSummary,
  explanationDetail,
  factorText,
  formatTime,
  recommendationText,
} from '@/lib/experience';
import { verificationActionLabel, verificationHref } from '@/lib/verification';

/** Mastery state with a plain-language reason; the numbers stay behind "Why?". */
export function MasteryPanel({ mastery }: { mastery: MasteryExplanation }) {
  return (
    <section className="space-y-3" data-testid="mastery-panel" data-state={mastery.state}>
      <div className="flex flex-wrap items-center gap-2">
        <MasteryBadge state={mastery.state} />
      </div>
      <p className="text-base" data-testid="mastery-headline">
        {MASTERY[mastery.state].headline}
      </p>
      <p className="text-sm text-muted-foreground" data-testid="mastery-detail">
        {explanationDetail(mastery.explanation_code)}
      </p>
      <details className="rounded-lg border p-3 text-sm" data-testid="mastery-why">
        <summary className="cursor-pointer font-medium">Why?</summary>
        <div className="mt-3 space-y-3">
          <ul className="space-y-1">
            {mastery.gates.map((gate) => (
              <li key={gate.code} className="flex items-start gap-2" data-testid="mastery-gate" data-met={gate.met}>
                <span aria-hidden="true">{gate.met ? '✓' : '○'}</span>
                <span>
                  {GATE_LABELS[gate.code] ?? gate.code}
                  {gate.current !== null && gate.required !== null ? (
                    <span className="text-muted-foreground">
                      {' '}
                      ({gate.code === 'STRONG_RESULTS' || gate.code === 'VERIFIED_RESULTS' ? 'result estimate' : 'evidence weight'}{' '}
                      {gate.current.toFixed(2)},
                      needs {gate.required.toFixed(2)})
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            {mastery.evidence_count} counted evidence event{mastery.evidence_count === 1 ? '' : 's'}, of which{' '}
            {mastery.performance_evidence_count} show your own performance
            {mastery.excluded_evidence_count > 0 ? `; ${mastery.excluded_evidence_count} not counted at your request` : ''}.
            Only independent evidence changes this state; the AI doing the work never counts as yours.
          </p>
          <p className="text-xs text-muted-foreground">
            Calculated {formatTime(mastery.computed_as_of)} · {mastery.algorithm_version}
          </p>
        </div>
      </details>
    </section>
  );
}

/** AI Assistance Debt as a reliance signal with its contributing factors - never a score headline. */
export function DebtPanel({ debt }: { debt: DebtExplanation }) {
  const summary = debtSummary(debt);
  return (
    <section className="space-y-3" data-testid="debt-panel" data-band={debt.band}>
      <div className="flex flex-wrap items-center gap-2">
        <DebtBadge band={debt.band} />
      </div>
      <p data-testid="debt-headline">{summary.headline}</p>
      <p className="text-sm text-muted-foreground" data-testid="debt-detail">
        {summary.detail}
      </p>
      {debt.eligible ? (
        <>
          <ul className="space-y-1 text-sm" data-testid="debt-factors">
            {debt.factors.map((factor) => (
              <li key={factor.code} className="flex flex-wrap gap-x-2" data-testid="debt-factor" data-code={factor.code}>
                <span className="font-medium">{LEVEL_LABEL[factor.level]}</span>
                <span className="text-muted-foreground">{factorText(factor, debt.verification)}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm text-muted-foreground">{DEBT_NOT_A_JUDGEMENT}</p>
          <details className="text-xs text-muted-foreground" data-testid="debt-why">
            <summary className="cursor-pointer">Internal detail</summary>
            <p className="mt-1">
              Internal reliance score {debt.score.toFixed(1)} / 100 ({debt.recent_delegation_count} recent delegations;
              eligibility needs {debt.min_recent_delegations}). It drives this explanation and is not a grade.
            </p>
          </details>
        </>
      ) : null}
    </section>
  );
}

export function RecommendationCard({ rec, showSkill = true }: { rec: Recommendation; showSkill?: boolean }) {
  const text = recommendationText(rec);
  const check = verificationHref(rec);
  return (
    <div className="space-y-1" data-testid="recommendation" data-type={rec.type} data-skill-id={rec.skill_id}>
      <p className="font-medium" data-testid="recommendation-title">
        {text.title}
      </p>
      {showSkill ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Link href={`/skills/${rec.skill_id}`} className="underline underline-offset-4">
            {rec.canonical_name}
          </Link>
          <MasteryBadge state={rec.mastery_state} />
        </p>
      ) : null}
      <p className="text-sm text-muted-foreground">{text.body}</p>
      {rec.type === 'PREREQUISITE' && rec.related_skill_id ? (
        <Link href={`/skills/${rec.related_skill_id}`} className="text-sm underline underline-offset-4">
          Open {rec.related_skill_name ?? 'the prerequisite'}
        </Link>
      ) : null}
      {check ? (
        <Link
          href={check}
          className="inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
          data-testid="recommendation-verify"
        >
          {verificationActionLabel(rec)}
        </Link>
      ) : null}
      {rec.verify_deferred ? (
        <p className="text-xs text-muted-foreground">A verification is queued behind higher-priority skills.</p>
      ) : null}
    </div>
  );
}
