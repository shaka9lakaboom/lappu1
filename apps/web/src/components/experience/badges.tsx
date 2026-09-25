import type { DebtBand, MasteryState } from '@skillmirror/contracts';
import type { ReactNode } from 'react';

import { DEBT_BAND, MASTERY, TONE_CLASSES, type Tone } from '@/lib/experience';
import { cn } from '@/lib/utils';

export function ToneBadge({ tone, children, className, testId }: { tone: Tone; children: ReactNode; className?: string; testId?: string }) {
  return (
    <span
      className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap', TONE_CLASSES[tone], className)}
      data-tone={tone}
      data-testid={testId}
    >
      {children}
    </span>
  );
}

/** A mastery state. UNKNOWN is neutral ("Not enough activity yet"), never a failure colour. */
export function MasteryBadge({ state, className }: { state: MasteryState; className?: string }) {
  const presentation = MASTERY[state];
  return (
    <span data-state={state} className="inline-flex">
      <ToneBadge tone={presentation.tone} className={className} testId="mastery-badge">
        {presentation.label}
      </ToneBadge>
    </span>
  );
}

/** The qualitative AI Assistance Debt signal (never the 0-100 score). */
export function DebtBadge({ band }: { band: DebtBand }) {
  const presentation = DEBT_BAND[band];
  return (
    <span data-band={band} className="inline-flex">
      <ToneBadge tone={presentation.tone} testId="debt-badge">
        {presentation.label}
      </ToneBadge>
    </span>
  );
}
