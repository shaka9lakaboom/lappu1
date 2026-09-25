import type { MasteryState } from '@skillmirror/contracts';

import { TONE_CLASSES } from '@/lib/experience';
import { stateSegments } from '@/lib/teacher';
import { cn } from '@/lib/utils';

/**
 * One stacked bar of the class's mastery states with a text legend (the numbers are always
 * written out; colour is never the only carrier). "Not enough evidence yet" is neutral grey.
 */
export function StateBar({ states, label }: { states: Record<MasteryState, number>; label: string }) {
  const segments = stateSegments(states);
  const shown = segments.filter((s) => s.count > 0);
  return (
    <figure className="space-y-2" data-testid="state-bar">
      <div className="flex h-4 w-full overflow-hidden rounded-full border" role="img" aria-label={label}>
        {shown.map((s) => (
          <div
            key={s.state}
            className={cn('h-full border-r last:border-r-0', TONE_CLASSES[s.tone])}
            style={{ width: `${Math.max(s.percent, 1)}%` }}
            data-state={s.state}
            title={`${s.label}: ${s.count}`}
          />
        ))}
      </div>
      <figcaption>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" data-testid="state-legend">
          {segments.map((s) => (
            <li key={s.state} data-state={s.state} className="flex items-center gap-1.5">
              <span className={cn('inline-block size-2.5 rounded-sm border', TONE_CLASSES[s.tone])} aria-hidden />
              {s.label}: <span className="font-medium tabular-nums text-foreground">{s.count}</span>
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}
