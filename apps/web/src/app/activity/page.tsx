import type { ActivityResponse, ActivityRow } from '@skillmirror/contracts';
import Link from 'next/link';

import { submitFeedback } from '@/app/feedback/actions';
import { AppHeader } from '@/components/experience/app-header';
import { ActivityList } from '@/components/experience/activity-list';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ACTIVITY_COLUMNS, type ActivityRow as FeedRow } from '@/lib/activity';
import { ApiError, apiRequest } from '@/lib/api';
import { isUuid } from '@/lib/course-selection';
import { requireApiSession } from '@/lib/session';
import { createSupabaseServerClient } from '@/lib/supabase/server';

const PAGE_SIZE = 50;

/** P1 activity_feed rows (raw capture only), used when the API is unavailable. */
function fromFeed(row: FeedRow): ActivityRow {
  return {
    ...row,
    processing_outcome: null,
    analyzed_in: null,
    segments: [],
  };
}

export default async function ActivityPage({ searchParams }: PageProps<'/activity'>) {
  const { accessToken } = await requireApiSession('/activity');
  const params = await searchParams;
  const before = typeof params.before === 'string' && !Number.isNaN(Date.parse(params.before)) ? params.before : null;
  const focus = (typeof params.focus === 'string' ? params.focus.split(',') : []).filter(isUuid).slice(0, 10);
  const supabase = await createSupabaseServerClient();

  let rows: ActivityRow[] = [];
  let focused: ActivityRow[] = [];
  let nextBefore: string | null = null;
  let degraded: string | null = null;
  let loadError: string | null = null;
  try {
    const page = await apiRequest<ActivityResponse>(
      `/v1/activity?limit=${PAGE_SIZE}${before ? `&before=${encodeURIComponent(before)}` : ''}`,
      accessToken,
    );
    rows = page.items;
    nextBefore = page.next_before;
    if (focus.length > 0) {
      focused = (
        await apiRequest<ActivityResponse>(
          `/v1/activity?${focus.map((id) => `raw_message_id=${id}`).join('&')}`,
          accessToken,
        )
      ).items;
    }
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    // The raw capture list still works without the API (row-level security limits it to you).
    degraded = error.message;
    const { data, error: feedError } = await supabase
      .from('activity_feed')
      .select(ACTIVITY_COLUMNS)
      .order('received_at', { ascending: false })
      .order('message_index', { ascending: false, nullsFirst: false })
      .limit(PAGE_SIZE)
      .returns<FeedRow[]>();
    if (feedError) loadError = feedError.message;
    rows = (data ?? []).map(fromFeed);
  }
  const { count } = await supabase.from('raw_messages').select('id', { count: 'exact', head: true });

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
      <AppHeader
        current="/activity"
        title="Activity"
        description="What the SkillMirror Companion captured, how it was treated, and the skills it was matched to."
      />

      {degraded ? (
        <ErrorNotice
          title="Skill details are unavailable right now"
          message={`${degraded} Showing the captured messages only.`}
        />
      ) : null}

      {focused.length > 0 ? (
        <Card data-testid="activity-focus">
          <CardHeader>
            <CardTitle>Source of your evidence</CardTitle>
            <CardDescription>The captured messages this evidence came from.</CardDescription>
          </CardHeader>
          <CardContent>
            <ActivityList rows={focused} returnTo="/activity" submit={submitFeedback} />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Captured messages</CardTitle>
          <CardDescription>
            <span data-testid="activity-count">{count ?? rows.length}</span> stored. Each turn is analysed in the background:
            non-learning activity is ignored, learning activity is matched to course skills, and only your own work
            counts as evidence of your skills. Use “Wrong skill” or “Don&apos;t count this” to correct it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadError ? (
            <p role="alert" className="text-sm text-destructive">
              Could not load activity: {loadError}
            </p>
          ) : rows.length === 0 ? (
            <div data-testid="activity-empty">
              <EmptyState title="Nothing captured yet">
                Install the Companion, sign in to it and use ChatGPT as usual.
              </EmptyState>
            </div>
          ) : (
            <ActivityList rows={rows} returnTo="/activity" submit={submitFeedback} />
          )}
          {nextBefore ? (
            <Link
              href={`/activity?before=${encodeURIComponent(nextBefore)}`}
              className="mt-4 inline-block text-sm underline underline-offset-4"
              data-testid="activity-older"
            >
              Show older activity
            </Link>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
