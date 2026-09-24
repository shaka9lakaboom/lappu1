import { PRODUCT_NAME } from '@skillmirror/config';
import { useEffect, useState } from 'react';

import type { GetStatusRequest, StatusResponse } from '../shared/messages';

type WorkerState = { kind: 'loading' } | { kind: 'ok'; status: StatusResponse } | { kind: 'error'; message: string };

const rowStyle = { display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0' } as const;

export function App() {
  const [worker, setWorker] = useState<WorkerState>({ kind: 'loading' });

  useEffect(() => {
    const request: GetStatusRequest = { type: 'GET_STATUS' };
    chrome.runtime
      .sendMessage<GetStatusRequest, StatusResponse>(request)
      .then((status) => setWorker({ kind: 'ok', status }))
      .catch((error: unknown) =>
        setWorker({ kind: 'error', message: error instanceof Error ? error.message : String(error) }),
      );
  }, []);

  return (
    <main style={{ padding: 16 }}>
      <h1 style={{ fontSize: 16, margin: '0 0 12px' }}>{PRODUCT_NAME} Companion</h1>
      <div style={rowStyle}>
        <span>Version</span>
        <span data-testid="version">{chrome.runtime.getManifest().version}</span>
      </div>
      <div style={rowStyle}>
        <span>Service worker</span>
        <span data-testid="worker-status">
          {worker.kind === 'loading' ? 'Connecting…' : worker.kind === 'ok' ? 'Running' : `Error: ${worker.message}`}
        </span>
      </div>
      <div style={rowStyle}>
        <span>Tracking</span>
        <span data-testid="tracking-status">Not available yet</span>
      </div>
      <p style={{ margin: '12px 0 0', fontSize: 12, opacity: 0.7 }}>
        ChatGPT capture is not part of this build. Nothing is being recorded.
      </p>
    </main>
  );
}
