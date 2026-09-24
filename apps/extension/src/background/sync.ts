/**
 * Flushes the LocalQueue to POST /v1/events/batch.
 *
 * - Items are deleted only when the response acknowledges them.
 * - Network errors, 429 and 5xx back off exponentially (5 s .. 10 min) and
 *   keep every item.
 * - 401 triggers one token refresh; if that fails the learner must sign in again.
 * - 422/413 split the batch to isolate a bad event; a single event the API
 *   still rejects is quarantined (kept locally, never retried).
 */
import {
  INGESTION_LIMITS,
  type EventBatchRequest,
  type EventBatchResponse,
} from '@skillmirror/contracts';

import type { LocalQueue, QueueItem } from '../capture/LocalQueue';
import type { StoredSession } from './auth';

export interface SyncAuth {
  getSession(): Promise<StoredSession | null>;
  refresh(): Promise<StoredSession | null>;
}

export interface LastSync {
  at: number;
  ok: boolean;
  accepted: number;
  duplicates: number;
  message: string | null;
}

export interface SyncState {
  lastSync: LastSync | null;
  lastSuccessAt: number | null;
  consecutiveFailures: number;
  backoffUntil: number;
}

export type FlushOutcome = 'idle' | 'synced' | 'signed_out' | 'backoff' | 'retry_later' | 'auth_required' | 'unconfigured';

const STATE_KEY = 'sync.state';
const BASE_BACKOFF_MS = 5_000;
const MAX_BACKOFF_MS = 10 * 60_000;

export const INITIAL_SYNC_STATE: SyncState = { lastSync: null, lastSuccessAt: null, consecutiveFailures: 0, backoffUntil: 0 };

export interface SyncEngineOptions {
  queue: LocalQueue;
  auth: SyncAuth;
  apiUrl: string | null;
  client: EventBatchRequest['client'];
  fetchImpl?: typeof fetch;
  now?: () => number;
  batchSize?: number;
}

type SendResult =
  | { kind: 'ok'; response: EventBatchResponse }
  | { kind: 'unauthorized' }
  | { kind: 'rejected'; status: number; detail: string }
  | { kind: 'transient'; detail: string };

export class SyncEngine {
  private running: Promise<FlushOutcome> | null = null;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;
  private readonly batchSize: number;

  constructor(private readonly options: SyncEngineOptions) {
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
    this.now = options.now ?? Date.now;
    this.batchSize = Math.min(options.batchSize ?? 25, INGESTION_LIMITS.maxEventsPerBatch);
  }

  async state(): Promise<SyncState> {
    return (await this.options.queue.getValue<SyncState>(STATE_KEY)) ?? INITIAL_SYNC_STATE;
  }

  private async saveState(patch: Partial<SyncState>): Promise<SyncState> {
    const next = { ...(await this.state()), ...patch };
    await this.options.queue.setValue(STATE_KEY, next);
    return next;
  }

  /** Single-flight: concurrent calls share one run. `force` ignores backoff (manual "Sync now"). */
  flush({ force = false }: { force?: boolean } = {}): Promise<FlushOutcome> {
    this.running ??= this.run(force).finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async run(force: boolean): Promise<FlushOutcome> {
    if (!this.options.apiUrl) return 'unconfigured';
    const state = await this.state();
    if (!force && state.backoffUntil > this.now()) return 'backoff';

    let session = await this.options.auth.getSession().catch(() => null);
    if (!session) return 'signed_out';

    let refreshed = false;
    let outcome: FlushOutcome = 'idle';
    for (;;) {
      const items = await this.options.queue.peek(session.user.id, this.batchSize);
      if (items.length === 0) return outcome;

      const result = await this.sendIsolating(items, session);
      if (result === 'unauthorized') {
        if (refreshed) {
          await this.recordFailure('Session rejected by the API. Sign in again.', false);
          return 'auth_required';
        }
        refreshed = true;
        session = await this.options.auth.refresh().catch(() => null);
        if (!session) {
          await this.recordFailure('Session expired. Sign in again.', false);
          return 'auth_required';
        }
        continue;
      }
      if (result === 'transient') return 'retry_later';
      outcome = 'synced';
    }
  }

  /** Sends items; on a validation rejection, bisects down to the offending events. */
  private async sendIsolating(items: QueueItem[], session: StoredSession): Promise<'ok' | 'unauthorized' | 'transient'> {
    const result = await this.send(items, session);
    switch (result.kind) {
      case 'ok': {
        const acked = new Set(
          result.response.results
            .filter((r) => r.status === 'accepted' || r.status === 'duplicate')
            .map((r) => r.event_id),
        );
        const confirmed = items.filter((i) => acked.has(i.event_id));
        await this.options.queue.acknowledge(confirmed, this.now());
        if (confirmed.length < items.length) {
          // Never assume storage for an event the API did not acknowledge.
          await this.recordFailure('Incomplete acknowledgement from the API.', true);
          return 'transient';
        }
        const accepted = result.response.accepted;
        const duplicates = result.response.duplicates;
        await this.saveState({
          lastSync: { at: this.now(), ok: true, accepted, duplicates, message: null },
          lastSuccessAt: this.now(),
          consecutiveFailures: 0,
          backoffUntil: 0,
        });
        return 'ok';
      }
      case 'unauthorized':
        return 'unauthorized';
      case 'transient':
        await this.options.queue.recordAttempt(items);
        await this.recordFailure(result.detail, true);
        return 'transient';
      case 'rejected': {
        if (items.length > 1) {
          const middle = Math.ceil(items.length / 2);
          for (const half of [items.slice(0, middle), items.slice(middle)]) {
            const outcome = await this.sendIsolating(half, session);
            if (outcome !== 'ok') return outcome;
          }
          return 'ok';
        }
        await this.options.queue.quarantine(items[0], `HTTP ${result.status}: ${result.detail}`, this.now());
        await this.saveState({
          lastSync: { at: this.now(), ok: false, accepted: 0, duplicates: 0, message: `An event was rejected (HTTP ${result.status}) and set aside.` },
        });
        return 'ok';
      }
    }
  }

  private async send(items: QueueItem[], session: StoredSession): Promise<SendResult> {
    const body: EventBatchRequest = { client: this.options.client, events: items.map((i) => i.envelope) };
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.options.apiUrl}/v1/events/batch`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
          'Idempotency-Key': items[0].event_id,
        },
        body: JSON.stringify(body),
      });
    } catch (error) {
      return { kind: 'transient', detail: error instanceof Error ? error.message : 'network error' };
    }
    if (response.status === 401) return { kind: 'unauthorized' };
    const detail = await response
      .clone()
      .json()
      .then((d: { detail?: unknown }) => (typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail ?? '')))
      .catch(() => '');
    if (response.ok) {
      const parsed = (await response.json().catch(() => null)) as EventBatchResponse | null;
      if (!parsed || !Array.isArray(parsed.results)) return { kind: 'transient', detail: 'malformed acknowledgement' };
      return { kind: 'ok', response: parsed };
    }
    if (response.status === 422 || response.status === 413) {
      return { kind: 'rejected', status: response.status, detail: detail.slice(0, 300) };
    }
    // 403 (identity mismatch / no profile), 429, 5xx: keep everything and retry later.
    return { kind: 'transient', detail: `HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ''}` };
  }

  private async recordFailure(message: string, backoff: boolean): Promise<void> {
    const state = await this.state();
    const failures = state.consecutiveFailures + 1;
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** Math.min(failures - 1, 16), MAX_BACKOFF_MS);
    await this.saveState({
      lastSync: { at: this.now(), ok: false, accepted: 0, duplicates: 0, message },
      consecutiveFailures: failures,
      backoffUntil: backoff ? this.now() + delay : state.backoffUntil,
    });
  }
}
