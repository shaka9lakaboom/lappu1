/**
 * SkillMirror Companion service worker: session, durable queue, API sync.
 *
 * Content scripts hand captured envelopes here; the worker binds the signed-in
 * learner, stores them in the LocalQueue and flushes the queue to the API
 * immediately, on a one-minute alarm, and when the network returns.
 */
import type { UnboundEnvelope } from '../capture/CaptureManager';
import { LocalQueue } from '../capture/LocalQueue';
import { CHATGPT_ADAPTER_VERSION } from '../content/adapters/ChatGPTAdapter';
import { CONFIG, isConfigured } from '../shared/config';
import {
  isContentRequest,
  isPopupRequest,
  STORAGE_KEYS,
  type ActionResponse,
  type PopupRequest,
  type StatusResponse,
} from '../shared/messages';
import { AuthError, SupabaseAuthClient } from './auth';
import { SyncEngine, type FlushOutcome } from './sync';

const workerStartedAt = new Date().toISOString();
const version = chrome.runtime.getManifest().version;
const FLUSH_ALARM = 'skillmirror-flush';
const SUPPORTED_CONTENT_ORIGINS = new Set(['https://chatgpt.com']);

const queue = new LocalQueue();
const auth =
  CONFIG.supabaseUrl && CONFIG.supabaseAnonKey
    ? new SupabaseAuthClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey, queue)
    : null;
const sync = new SyncEngine({
  queue,
  auth: {
    getSession: () => auth?.getSession() ?? Promise.resolve(null),
    refresh: () => auth?.refresh() ?? Promise.resolve(null),
  },
  apiUrl: CONFIG.apiUrl,
  client: { extension_version: version, adapter_version: CHATGPT_ADAPTER_VERSION },
});

/** Flushes, and withdraws capture permission if the session turned out to be over. */
async function flush(force = false): Promise<FlushOutcome> {
  const outcome = await sync.flush({ force });
  if (outcome === 'auth_required' || outcome === 'signed_out') await refreshCaptureFlag();
  return outcome;
}

async function isPaused(): Promise<boolean> {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.paused);
  return stored[STORAGE_KEYS.paused] === true;
}

/** Publishes the one flag content scripts need: may capture right now. */
async function refreshCaptureFlag(): Promise<boolean> {
  const session = auth ? await auth.current() : undefined;
  const enabled = isConfigured() && Boolean(session) && !(await isPaused());
  await chrome.storage.local.set({ [STORAGE_KEYS.captureEnabled]: enabled });
  return enabled;
}

async function status(): Promise<StatusResponse> {
  const session = auth ? await auth.current() : undefined;
  const [paused, queued, quarantined, state] = await Promise.all([
    isPaused(),
    session ? queue.count(session.user.id) : Promise.resolve(0),
    queue.countQuarantined(),
    sync.state(),
  ]);
  return {
    type: 'STATUS',
    version,
    workerStartedAt,
    configured: isConfigured(),
    signedIn: Boolean(session),
    email: session?.user.email ?? null,
    paused,
    captureEnabled: isConfigured() && Boolean(session) && !paused,
    queued,
    quarantined,
    lastSync: state.lastSync,
    lastSuccessAt: state.lastSuccessAt,
    backoffUntil: state.backoffUntil,
    webUrl: CONFIG.webUrl,
  };
}

async function handlePopup(request: PopupRequest): Promise<StatusResponse | ActionResponse> {
  switch (request.type) {
    case 'GET_STATUS':
      return status();
    case 'SIGN_IN': {
      if (!auth) return { ok: false, error: 'This build is not configured with a Supabase project.' };
      try {
        await auth.signInWithPassword(request.email.trim(), request.password);
      } catch (error) {
        return { ok: false, error: error instanceof AuthError ? error.message : 'Sign-in failed.' };
      }
      await refreshCaptureFlag();
      void flush(true);
      return { ok: true };
    }
    case 'SIGN_OUT':
      await auth?.signOut();
      await refreshCaptureFlag();
      return { ok: true };
    case 'SET_PAUSED':
      await chrome.storage.local.set({ [STORAGE_KEYS.paused]: request.paused });
      await refreshCaptureFlag();
      return { ok: true };
    case 'SYNC_NOW': {
      const outcome = await flush(true);
      return { ok: outcome === 'synced' || outcome === 'idle', error: outcome };
    }
  }
}

async function handleCapture(events: UnboundEnvelope[]): Promise<ActionResponse> {
  const session = auth ? await auth.current() : undefined;
  if (!session) return { ok: false, error: 'signed_out' };
  if (await isPaused()) return { ok: false, error: 'paused' };
  await queue.enqueue(
    session.user.id,
    events.map((event) => ({ ...event, learner_id: session.user.id })),
  );
  void flush();
  return { ok: true };
}

/** The popup (or another page of this extension, possibly opened in a tab). */
function isExtensionPage(sender: chrome.runtime.MessageSender): boolean {
  return sender.id === chrome.runtime.id && (sender.url ?? '').startsWith(chrome.runtime.getURL(''));
}

function isSupportedContentScript(sender: chrome.runtime.MessageSender): boolean {
  if (sender.id !== chrome.runtime.id || !sender.tab) return false;
  try {
    return SUPPORTED_CONTENT_ORIGINS.has(sender.origin ?? new URL(sender.url ?? '').origin);
  } catch {
    return false;
  }
}

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  let work: Promise<unknown> | null = null;
  if (isExtensionPage(sender) && isPopupRequest(message)) work = handlePopup(message);
  else if (isSupportedContentScript(sender) && isContentRequest(message)) work = handleCapture(message.events);
  if (!work) return false;
  work.then(sendResponse, (error: unknown) =>
    sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }),
  );
  return true; // respond asynchronously
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) void flush();
});

async function init(): Promise<void> {
  const existing = await chrome.alarms.get(FLUSH_ALARM);
  if (!existing) await chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: 1 });
  await refreshCaptureFlag();
  void flush();
}

chrome.runtime.onInstalled.addListener(() => void init());
chrome.runtime.onStartup.addListener(() => void init());
self.addEventListener('online', () => void flush(true));
void init();
