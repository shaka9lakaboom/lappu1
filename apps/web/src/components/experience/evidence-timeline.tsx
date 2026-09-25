import type { EvidenceTimelineItem } from '@skillmirror/contracts';
import Link from 'next/link';

import type { FeedbackFormState } from '@/app/feedback/actions';
import { ToneBadge } from '@/components/experience/badges';
import { CorrectionButton } from '@/components/experience/correction-button';
import {
  ACTOR_LABEL,
  EVIDENCE_TYPE_LABEL,
  OUTCOME_LABEL,
  exclusionLabel,
  formatTime,
  independenceLabel,
  percent,
} from '@/lib/experience';

type Submit = (state: FeedbackFormState, formData: FormData) => Promise<FeedbackFormState>;

function Span({ label, text }: { label: string; text: string | null | undefined }) {
  if (!text) return null;
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      {/* Captured text is untrusted: plain text only. */}
      <dd className="whitespace-pre-wrap break-words rounded-md bg-muted px-2 py-1 font-mono text-xs" data-testid="evidence-span">
        {text}
      </dd>
    </div>
  );
}

/** Every EvidenceEvent behind the skill, newest first; excluded ones stay visible, marked. */
export function EvidenceTimeline({ items, returnTo, submit }: { items: EvidenceTimelineItem[]; returnTo: string; submit: Submit }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="evidence-empty">
        No evidence yet. Evidence appears here once your captured activity involves this skill.
      </p>
    );
  }
  return (
    <ol className="space-y-3" data-testid="evidence-timeline">
      {items.map((item) => {
        const event = item.event;
        const spans = event.evidence_span;
        const sourceIds = item.source?.raw_message_ids ?? [];
        return (
          <li
            key={event.id}
            className="rounded-lg border p-3 text-sm"
            data-testid="evidence-item"
            data-evidence-id={event.id}
            data-excluded={event.excluded}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{EVIDENCE_TYPE_LABEL[event.evidence_type]}</span>
              <ToneBadge tone="neutral">Actor: {ACTOR_LABEL[event.actor]}</ToneBadge>
              <span className="text-muted-foreground">{OUTCOME_LABEL[event.outcome_signal]}</span>
              <span className="text-muted-foreground">· {independenceLabel(event.independence)}</span>
              <span className="text-muted-foreground">· {formatTime(event.occurred_at)}</span>
              {event.excluded ? (
                <ToneBadge tone="neutral" testId="evidence-excluded">
                  {exclusionLabel(event.exclusion_reason)}
                </ToneBadge>
              ) : (
                <span className="text-xs text-muted-foreground" data-testid="evidence-counted">
                  {item.counts_toward_mastery
                    ? 'Counts toward mastery'
                    : item.counts_toward_debt
                      ? 'Counts toward the reliance signal only'
                      : 'Recorded, not counted toward mastery'}
                </span>
              )}
            </div>
            <details className="mt-2" data-testid="evidence-why">
              <summary className="cursor-pointer text-muted-foreground">Why?</summary>
              <div className="mt-2 space-y-3">
                <dl className="space-y-2">
                  <Span label="Your words" text={spans.student} />
                  <Span label="The AI's part" text={spans.ai} />
                  <Span label="Why it matched this skill" text={spans.mapping} />
                  {Object.entries(spans)
                    .filter(([k]) => !['student', 'ai', 'mapping'].includes(k))
                    .map(([k, v]) => (
                      <Span key={k} label={k} text={v} />
                    ))}
                </dl>
                <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                  <div>
                    <dt className="inline text-muted-foreground">Skill match confidence: </dt>
                    <dd className="inline" data-testid="mapping-confidence">{percent(event.mapping_confidence)}</dd>
                  </div>
                  <div>
                    <dt className="inline text-muted-foreground">Who-did-what confidence: </dt>
                    <dd className="inline" data-testid="attribution-confidence">{percent(event.attribution_confidence)}</dd>
                  </div>
                  <div>
                    <dt className="inline text-muted-foreground">Reason code: </dt>
                    <dd className="inline font-mono text-xs" data-testid="reason-code">{item.reason_code ?? '—'}</dd>
                  </div>
                  <div>
                    <dt className="inline text-muted-foreground">Qualification: </dt>
                    <dd className="inline font-mono text-xs">{event.qualification_reason}</dd>
                  </div>
                  <div>
                    <dt className="inline text-muted-foreground">Current weight: </dt>
                    <dd className="inline">{item.current_weight.toFixed(2)}</dd>
                  </div>
                  <div>
                    <dt className="inline text-muted-foreground">Source: </dt>
                    <dd className="inline">{event.source_type === 'AI_ACTIVITY' ? 'Captured AI conversation' : event.source_type}</dd>
                  </div>
                </dl>
                {item.source?.learner_message_preview ? (
                  <p className="whitespace-pre-wrap break-words text-muted-foreground" data-testid="source-preview">
                    “{item.source.learner_message_preview}”
                  </p>
                ) : null}
                {sourceIds.length > 0 ? (
                  <Link
                    href={`/activity?focus=${sourceIds.join(',')}`}
                    className="underline underline-offset-4"
                    data-testid="source-link"
                  >
                    Open the source activity
                  </Link>
                ) : null}
                {item.correction ? (
                  <p className="text-muted-foreground" data-testid="evidence-correction">
                    {item.correction.action === 'WRONG_SKILL' ? 'You marked this as the wrong skill' : 'You chose not to count this'} on{' '}
                    {formatTime(item.correction.created_at)}
                    {item.correction.note ? ` — “${item.correction.note}”` : ''}. The record is kept for transparency.
                  </p>
                ) : null}
              </div>
            </details>
            {event.source_type === 'AI_ACTIVITY' && !event.excluded ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <CorrectionButton action="DONT_COUNT" targetType="EVIDENCE_EVENT" targetId={event.id} returnTo={returnTo} submit={submit} />
                <CorrectionButton action="WRONG_SKILL" targetType="EVIDENCE_EVENT" targetId={event.id} returnTo={returnTo} submit={submit} />
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
