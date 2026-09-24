import Link from 'next/link';
import { redirect } from 'next/navigation';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ACTIVITY_COLUMNS,
  processingLabel,
  providerLabel,
  shortId,
  shortPreview,
  type ActivityRow,
} from '@/lib/activity';
import { createSupabaseServerClient } from '@/lib/supabase/server';

const PAGE_SIZE = 100;

export default async function ActivityPage() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in?next=/activity');

  // Row-level security limits activity_feed to this learner's own rows.
  const [{ data, error }, { count }] = await Promise.all([
    supabase
      .from('activity_feed')
      .select(ACTIVITY_COLUMNS)
      .order('received_at', { ascending: false })
      .order('message_index', { ascending: false, nullsFirst: false })
      .limit(PAGE_SIZE)
      .returns<ActivityRow[]>(),
    supabase.from('raw_messages').select('id', { count: 'exact', head: true }),
  ]);
  const rows = data ?? [];

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-12">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
          <p className="text-sm text-muted-foreground">
            Conversation messages captured by the SkillMirror Companion and stored for your account.
          </p>
        </div>
        <Link href="/dashboard" className="text-sm underline underline-offset-4">
          Back to dashboard
        </Link>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Captured messages</CardTitle>
          <CardDescription>
            <span data-testid="activity-count">{count ?? rows.length}</span> stored. Messages are only recorded here;
            skills are not analysed yet.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              Could not load activity: {error.message}
            </p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="activity-empty">
              Nothing captured yet. Install the Companion, sign in to it and use ChatGPT.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm" data-testid="activity-table">
                <thead className="text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Captured</th>
                    <th className="py-2 pr-4 font-medium">Provider</th>
                    <th className="py-2 pr-4 font-medium">Role</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 font-medium">Preview</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-t align-top" data-testid="activity-row" data-message-id={row.id}>
                      <td className="whitespace-nowrap py-2 pr-4 font-mono text-xs">
                        <time dateTime={row.captured_at}>{new Date(row.captured_at).toISOString().replace('T', ' ').slice(0, 19)} UTC</time>
                        <div className="text-muted-foreground">conv {shortId(row.external_conversation_id)}</div>
                      </td>
                      <td className="py-2 pr-4">{providerLabel(row.source_provider)}</td>
                      <td className="py-2 pr-4" data-testid="activity-role">
                        {row.role}
                        {row.revision_index > 0 && <span className="text-muted-foreground"> (rev {row.revision_index})</span>}
                      </td>
                      <td className="py-2 pr-4" data-testid="activity-status">
                        <div>Synced</div>
                        <div className="text-muted-foreground">{processingLabel(row.processing_state)}</div>
                        {row.context_incomplete && <div className="text-muted-foreground">Attachment not captured</div>}
                      </td>
                      {/* Captured text is untrusted: rendered as plain text only. */}
                      <td className="max-w-md break-words py-2 pr-4" data-testid="activity-preview">
                        {shortPreview(row.preview, row.content_chars)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
