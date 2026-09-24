import { PRODUCT_NAME } from '@skillmirror/config';
import { useCallback, useEffect, useState, type FormEvent } from 'react';

import type {
  ActionResponse,
  ContentPingRequest,
  ContentStatusResponse,
  PopupRequest,
  StatusResponse,
} from '../shared/messages';

type WorkerState = { kind: 'loading' } | { kind: 'ok'; status: StatusResponse } | { kind: 'error'; message: string };
type PageState = { kind: 'checking' } | { kind: 'supported'; content: ContentStatusResponse } | { kind: 'unsupported' };

const rowStyle = { display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0' } as const;
const buttonStyle = { font: 'inherit', padding: '6px 10px', borderRadius: 6, border: '1px solid #8884', cursor: 'pointer' } as const;
const inputStyle = { font: 'inherit', padding: '6px 8px', borderRadius: 6, border: '1px solid #8886', width: '100%', boxSizing: 'border-box' } as const;

function send<T>(request: PopupRequest): Promise<T> {
  return chrome.runtime.sendMessage<PopupRequest, T>(request);
}

function Row({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div style={rowStyle}>
      <span>{label}</span>
      <span data-testid={testId} style={{ textAlign: 'right' }}>
        {value}
      </span>
    </div>
  );
}

function formatTime(ms: number | null): string {
  return ms ? new Date(ms).toLocaleTimeString() : 'Never';
}

async function checkActivePage(): Promise<PageState> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id === undefined) return { kind: 'unsupported' };
    // Only the ChatGPT content script answers; any other page has none.
    const content = await chrome.tabs.sendMessage<ContentPingRequest, ContentStatusResponse>(tab.id, { type: 'CONTENT_PING' });
    return content?.type === 'CONTENT_STATUS' ? { kind: 'supported', content } : { kind: 'unsupported' };
  } catch {
    return { kind: 'unsupported' };
  }
}

export function App() {
  const [worker, setWorker] = useState<WorkerState>({ kind: 'loading' });
  const [page, setPage] = useState<PageState>({ kind: 'checking' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setWorker({ kind: 'ok', status: await send<StatusResponse>({ type: 'GET_STATUS' }) });
    } catch (e) {
      setWorker({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
    }
    setPage(await checkActivePage());
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (request: PopupRequest) => {
    setBusy(true);
    setError(null);
    try {
      const result = await send<ActionResponse>(request);
      if (!result.ok && result.error && request.type !== 'SYNC_NOW') setError(result.error);
    } finally {
      setBusy(false);
      await refresh();
    }
  };

  const signIn = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void act({ type: 'SIGN_IN', email: String(data.get('email') ?? ''), password: String(data.get('password') ?? '') });
  };

  const status = worker.kind === 'ok' ? worker.status : null;
  const tracking = !status
    ? '…'
    : !status.configured
      ? 'Not configured'
      : !status.signedIn
        ? 'Off (signed out)'
        : status.paused
          ? 'Paused'
          : 'On';
  const lastSync = status?.lastSync;

  return (
    <main style={{ padding: 16 }}>
      <h1 style={{ fontSize: 16, margin: '0 0 12px' }}>{PRODUCT_NAME} Companion</h1>
      <Row label="Version" value={chrome.runtime.getManifest().version} testId="version" />
      <Row
        label="Service worker"
        value={worker.kind === 'loading' ? 'Connecting…' : worker.kind === 'ok' ? 'Running' : `Error: ${worker.message}`}
        testId="worker-status"
      />

      {status && !status.configured && (
        <p role="alert" style={{ fontSize: 12 }}>
          This build has no SkillMirror configuration. Rebuild with the Supabase URL, anon key and API URL set.
        </p>
      )}

      {status?.configured && !status.signedIn && (
        <form onSubmit={signIn} style={{ display: 'grid', gap: 8, margin: '12px 0' }} data-testid="sign-in-form">
          <input style={inputStyle} name="email" type="email" placeholder="SkillMirror email" autoComplete="email" required />
          <input style={inputStyle} name="password" type="password" placeholder="Password" autoComplete="current-password" required />
          <button style={buttonStyle} type="submit" disabled={busy}>
            Sign in to SkillMirror
          </button>
        </form>
      )}

      {status?.signedIn && <Row label="Signed in as" value={status.email ?? 'unknown'} testId="identity" />}
      <Row label="Tracking" value={tracking} testId="tracking-status" />
      <Row
        label="Provider"
        value={page.kind === 'checking' ? '…' : page.kind === 'supported' ? 'ChatGPT' : 'Unsupported page'}
        testId="provider-status"
      />
      {page.kind === 'unsupported' && (
        <p style={{ margin: '4px 0', fontSize: 12, opacity: 0.8 }} data-testid="unsupported-note">
          SkillMirror captures only on chatgpt.com. Nothing on this page is recorded.
        </p>
      )}
      <Row label="Queued events" value={status ? String(status.queued) : '…'} testId="queued-count" />
      <Row label="Last successful sync" value={status ? formatTime(status.lastSuccessAt) : '…'} testId="last-sync" />
      {lastSync && !lastSync.ok && (
        <p style={{ margin: '4px 0', fontSize: 12 }} data-testid="sync-error">
          Last attempt failed: {lastSync.message}
        </p>
      )}
      {status && status.quarantined > 0 && (
        <p style={{ margin: '4px 0', fontSize: 12 }}>{status.quarantined} event(s) were rejected by the API and set aside.</p>
      )}

      {error && (
        <p role="alert" style={{ margin: '8px 0', fontSize: 12, color: '#c33' }}>
          {error}
        </p>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
        {status?.signedIn && (
          <button style={buttonStyle} disabled={busy} data-testid="pause-toggle" onClick={() => void act({ type: 'SET_PAUSED', paused: !status.paused })}>
            {status.paused ? 'Resume tracking' : 'Pause tracking'}
          </button>
        )}
        {status?.signedIn && (
          <button style={buttonStyle} disabled={busy} onClick={() => void act({ type: 'SYNC_NOW' })}>
            Sync now
          </button>
        )}
        {status?.webUrl && (
          <a style={{ ...buttonStyle, textDecoration: 'none', color: 'inherit' }} href={`${status.webUrl}/activity`} target="_blank" rel="noreferrer" data-testid="dashboard-link">
            Open dashboard
          </a>
        )}
        {status?.signedIn && (
          <button style={buttonStyle} disabled={busy} onClick={() => void act({ type: 'SIGN_OUT' })}>
            Sign out
          </button>
        )}
      </div>
    </main>
  );
}
