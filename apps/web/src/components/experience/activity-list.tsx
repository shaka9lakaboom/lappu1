import type { ActivityRow, ActivitySegment } from '@skillmirror/contracts';
import Link from 'next/link';

import type { FeedbackFormState } from '@/app/feedback/actions';
import { ToneBadge } from '@/components/experience/badges';
import { CorrectionButton } from '@/components/experience/correction-button';
import { processingLabel, providerLabel, shortId, shortPreview } from '@/lib/activity';
import {
  ACTOR_LABEL,
  EVIDENCE_TYPE_LABEL,
  exclusionLabel,
  formatTime,
  percent,
  processingOutcomeLabel,
  qualificationNote,
  segmentOutcomeLabel,
} from '@/lib/experience';

type Submit = (state: FeedbackFormState, formData: FormData) => Promise<FeedbackFormState>;

function Segment({ segment, returnTo, submit }: { segment: ActivitySegment; returnTo: string; submit: Submit }) {
  const accepted = segment.mappings.filter((m) => m.status === 'ACCEPTED');
  const others = segment.mappings.length - accepted.length;
  const countable = segment.evidence_count > segment.excluded_evidence_count;
  return (
    <div className="space-y-2 rounded-md border p-2" data-testid="activity-segment" data-route={segment.route}>
      <p className="text-xs text-muted-foreground" data-testid="segment-outcome">
        {segment.segment_count > 1 ? `Part ${segment.segment_index + 1} of ${segment.segment_count}: ` : ''}
        {segmentOutcomeLabel(segment)}
        {segment.context_incomplete ? ' · attachment not captured' : ''}
      </p>
      {accepted.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {accepted.map((m) => (
            <li
              key={m.mapping_id}
              className="flex flex-wrap items-center gap-2"
              data-testid="skill-chip"
              data-mapping-id={m.mapping_id}
              data-excluded={m.excluded}
            >
              <Link href={`/skills/${m.skill_id}`} className="rounded-full border px-2 py-0.5 text-xs font-medium hover:bg-muted">
                {m.canonical_name}
              </Link>
              <span className="text-xs text-muted-foreground">match {percent(m.confidence)}</span>
              {m.actor ? <ToneBadge tone="neutral">Actor: {ACTOR_LABEL[m.actor]}</ToneBadge> : null}
              {m.evidence_type ? (
                <span className="text-xs text-muted-foreground" data-testid="chip-evidence-type">
                  {EVIDENCE_TYPE_LABEL[m.evidence_type]}
                  {qualificationNote(m.qualification_reason) ? (
                    <span data-testid="chip-qualification"> · {qualificationNote(m.qualification_reason)}</span>
                  ) : null}
                </span>
              ) : m.attribution_status ? (
                <span className="text-xs text-muted-foreground">No evidence qualified</span>
              ) : (
                <span className="text-xs text-muted-foreground">Attribution pending</span>
              )}
              {m.excluded || m.correction ? (
                <ToneBadge tone="neutral" testId="chip-excluded">
                  {m.correction?.action === 'WRONG_SKILL' ? 'Marked wrong skill' : exclusionLabel(m.exclusion_reason)}
                </ToneBadge>
              ) : (
                <CorrectionButton action="WRONG_SKILL" targetType="SKILL_MAPPING" targetId={m.mapping_id} returnTo={returnTo} submit={submit} />
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {others > 0 ? (
        <p className="text-xs text-muted-foreground">
          {others} other candidate skill{others === 1 ? ' was' : 's were'} not confident enough to count.
        </p>
      ) : null}
      {segment.correction ? (
        <ToneBadge tone="neutral" testId="segment-excluded">
          You chose not to count this activity
        </ToneBadge>
      ) : countable ? (
        <CorrectionButton action="DONT_COUNT" targetType="ACTIVITY_SEGMENT" targetId={segment.segment_id} returnTo={returnTo} submit={submit} />
      ) : null}
    </div>
  );
}

/** Captured messages with what SkillMirror derived from them. Captured text renders as plain text. */
export function ActivityList({ rows, returnTo, submit }: { rows: ActivityRow[]; returnTo: string; submit: Submit }) {
  return (
    <ol className="divide-y" data-testid="activity-table">
      {rows.map((row) => {
        const outcome = processingOutcomeLabel(row.processing_outcome);
        return (
          <li key={row.id} id={`activity-${row.id}`} className="grid gap-2 py-3 sm:grid-cols-[11rem_1fr]" data-testid="activity-row" data-message-id={row.id}>
            <div className="space-y-0.5 text-xs">
              <time dateTime={row.captured_at} className="font-mono">
                {formatTime(row.captured_at)}
              </time>
              <div className="text-muted-foreground">
                {providerLabel(row.source_provider)} · conv {shortId(row.external_conversation_id)}
              </div>
              <div data-testid="activity-role">
                {row.role}
                {row.revision_index > 0 ? <span className="text-muted-foreground"> (rev {row.revision_index})</span> : null}
              </div>
              <div data-testid="activity-status" className="text-muted-foreground">
                <div>Synced</div>
                <div>{processingLabel(row.processing_state)}</div>
                {outcome ? <div data-testid="activity-outcome">{outcome}</div> : null}
                {row.context_incomplete ? <div>Attachment not captured</div> : null}
              </div>
            </div>
            <div className="min-w-0 space-y-2">
              <p className="break-words text-sm" data-testid="activity-preview">
                {shortPreview(row.preview, row.content_chars)}
              </p>
              {row.segments.map((segment) => (
                <Segment key={segment.segment_id} segment={segment} returnTo={returnTo} submit={submit} />
              ))}
              {row.analyzed_in && row.analyzed_in !== row.id ? (
                <p className="text-xs text-muted-foreground">Analysed together with the message it answers.</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
