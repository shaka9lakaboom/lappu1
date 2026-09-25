import type { Course } from '@skillmirror/contracts';

import { LocalTime } from '@/components/local-time';
import { graphWait } from '@/lib/courses';

/** Why an in-progress skill graph is not being generated right now, and when it retries. */
export function GraphWaitNotice({ course, now }: { course: Course; now?: Date }) {
  const wait = graphWait(course, now);
  if (!wait) return null;
  return (
    <span className="block space-y-1" data-testid="graph-wait" data-reason={course.bootstrap_wait_reason ?? 'QUEUED'}>
      {wait.message ? <span className="block">{wait.message}</span> : null}
      {wait.overdue ? (
        <span className="block" data-testid="graph-wait-overdue">
          Due since <LocalTime iso={wait.nextAttemptAt} />, but no SkillMirror worker has picked it up yet. Check
          that the backend is running.
        </span>
      ) : wait.message ? (
        <span className="block" data-testid="graph-wait-retry">
          Retrying automatically around <LocalTime iso={wait.nextAttemptAt} />.
        </span>
      ) : null}
    </span>
  );
}
