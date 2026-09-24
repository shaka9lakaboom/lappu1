/**
 * Durable outbound queue in IndexedDB (architecture section 6.4).
 *
 * Lives in the service worker, i.e. the extension's own origin: the ChatGPT
 * page and content scripts cannot read it. Items survive browser restarts,
 * API outages and network loss. An item is deleted only after the backend has
 * acknowledged it as stored (accepted or duplicate), never merely because a
 * request was attempted.
 */
import type { RawActivityEnvelope } from '@skillmirror/contracts';

const DB_NAME = 'skillmirror-companion';
const DB_VERSION = 1;
const QUEUE = 'queue';
const SENT = 'sent';
const QUARANTINE = 'quarantine';
const KV = 'kv';
/** Recently acknowledged dedup keys kept to avoid re-queueing re-rendered history. */
const SENT_KEYS_LIMIT = 5000;

export interface QueueItem {
  event_id: string;
  learner_id: string;
  client_event_id: string;
  envelope: RawActivityEnvelope;
  enqueued_at: number;
  attempts: number;
}

interface SentKey {
  key: string; // `${learner_id}|${client_event_id}`
  acked_at: number;
}

interface QuarantinedItem {
  event_id: string;
  item: QueueItem;
  reason: string;
  at: number;
}

function promisify<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('transaction aborted'));
  });
}

export class LocalQueue {
  private dbPromise: Promise<IDBDatabase> | null = null;

  constructor(private readonly factory: IDBFactory = indexedDB) {}

  private db(): Promise<IDBDatabase> {
    this.dbPromise ??= new Promise((resolve, reject) => {
      const request = this.factory.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        const queue = db.createObjectStore(QUEUE, { keyPath: 'event_id' });
        queue.createIndex('by_learner_key', ['learner_id', 'client_event_id'], { unique: true });
        queue.createIndex('by_learner_time', ['learner_id', 'enqueued_at']);
        const sent = db.createObjectStore(SENT, { keyPath: 'key' });
        sent.createIndex('by_time', 'acked_at');
        db.createObjectStore(QUARANTINE, { keyPath: 'event_id' });
        db.createObjectStore(KV);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return this.dbPromise;
  }

  close(): void {
    void this.dbPromise?.then((db) => db.close());
    this.dbPromise = null;
  }

  /** Adds envelopes that are neither queued nor recently acknowledged. */
  async enqueue(learnerId: string, envelopes: RawActivityEnvelope[], now = Date.now()): Promise<{ added: number; skipped: number }> {
    const db = await this.db();
    const tx = db.transaction([QUEUE, SENT], 'readwrite');
    const queue = tx.objectStore(QUEUE);
    const sent = tx.objectStore(SENT);
    let added = 0;
    let skipped = 0;
    for (const envelope of envelopes) {
      const [queued, acked] = await Promise.all([
        promisify(queue.index('by_learner_key').getKey([learnerId, envelope.client_event_id])),
        promisify(sent.getKey(`${learnerId}|${envelope.client_event_id}`)),
      ]);
      if (queued !== undefined || acked !== undefined) {
        skipped++;
        continue;
      }
      const item: QueueItem = {
        event_id: envelope.event_id,
        learner_id: learnerId,
        client_event_id: envelope.client_event_id,
        envelope: { ...envelope, learner_id: learnerId },
        enqueued_at: now + added, // keeps insertion order within one call
        attempts: 0,
      };
      await promisify(queue.add(item));
      added++;
    }
    await done(tx);
    return { added, skipped };
  }

  /** Oldest items for one learner, in capture order. */
  async peek(learnerId: string, limit: number): Promise<QueueItem[]> {
    const db = await this.db();
    const index = db.transaction(QUEUE, 'readonly').objectStore(QUEUE).index('by_learner_time');
    const range = IDBKeyRange.bound([learnerId, -Infinity], [learnerId, Infinity]);
    return promisify(index.getAll(range, limit)) as Promise<QueueItem[]>;
  }

  async count(learnerId?: string): Promise<number> {
    const db = await this.db();
    const store = db.transaction(QUEUE, 'readonly').objectStore(QUEUE);
    if (!learnerId) return promisify(store.count());
    return promisify(store.index('by_learner_time').count(IDBKeyRange.bound([learnerId, -Infinity], [learnerId, Infinity])));
  }

  async countQuarantined(): Promise<number> {
    const db = await this.db();
    return promisify(db.transaction(QUARANTINE, 'readonly').objectStore(QUARANTINE).count());
  }

  /** Removes acknowledged items and remembers their keys. */
  async acknowledge(items: QueueItem[], now = Date.now()): Promise<void> {
    if (items.length === 0) return;
    const db = await this.db();
    const tx = db.transaction([QUEUE, SENT], 'readwrite');
    const queue = tx.objectStore(QUEUE);
    const sent = tx.objectStore(SENT);
    for (const item of items) {
      queue.delete(item.event_id);
      sent.put({ key: `${item.learner_id}|${item.client_event_id}`, acked_at: now } satisfies SentKey);
    }
    await done(tx);
    await this.pruneSent();
  }

  async recordAttempt(items: QueueItem[]): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(QUEUE, 'readwrite');
    const queue = tx.objectStore(QUEUE);
    for (const item of items) {
      const current = (await promisify(queue.get(item.event_id))) as QueueItem | undefined;
      if (current) queue.put({ ...current, attempts: current.attempts + 1 });
    }
    await done(tx);
  }

  /** Moves an item the backend permanently rejected out of the send path, keeping it for inspection. */
  async quarantine(item: QueueItem, reason: string, now = Date.now()): Promise<void> {
    const db = await this.db();
    const tx = db.transaction([QUEUE, QUARANTINE], 'readwrite');
    tx.objectStore(QUEUE).delete(item.event_id);
    tx.objectStore(QUARANTINE).put({ event_id: item.event_id, item, reason: reason.slice(0, 500), at: now } satisfies QuarantinedItem);
    await done(tx);
  }

  private async pruneSent(): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(SENT, 'readwrite');
    const store = tx.objectStore(SENT);
    const excess = (await promisify(store.count())) - SENT_KEYS_LIMIT;
    if (excess > 0) {
      const oldest = await promisify(store.index('by_time').getAllKeys(null, excess));
      for (const key of oldest) store.delete(key);
    }
    await done(tx);
  }

  async getValue<T>(key: string): Promise<T | undefined> {
    const db = await this.db();
    return promisify(db.transaction(KV, 'readonly').objectStore(KV).get(key)) as Promise<T | undefined>;
  }

  async setValue(key: string, value: unknown): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(KV, 'readwrite');
    if (value === undefined) tx.objectStore(KV).delete(key);
    else tx.objectStore(KV).put(value, key);
    await done(tx);
  }
}
