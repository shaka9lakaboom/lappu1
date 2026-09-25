/**
 * Runtime messages between extension contexts.
 *
 * popup -> service worker: GET_STATUS, SIGN_IN, SIGN_OUT, SET_PAUSED, SYNC_NOW
 * content script -> service worker: CAPTURE_EVENTS
 * popup -> content script: CONTENT_PING
 *
 * The service worker checks the sender of every message: account and settings
 * messages only from extension pages, captured events only from content
 * scripts on supported provider pages. Tokens never leave the worker.
 */
import type { UnboundEnvelope } from '../capture/CaptureManager';
import type { CaptureState } from '../capture/CaptureManager';
import type { CaptureStatus } from '../content/captureStatus';
import type { LastSync } from '../background/sync';

export interface GetStatusRequest {
  type: 'GET_STATUS';
}
export interface SignInRequest {
  type: 'SIGN_IN';
  email: string;
  password: string;
}
export interface SignOutRequest {
  type: 'SIGN_OUT';
}
export interface SetPausedRequest {
  type: 'SET_PAUSED';
  paused: boolean;
}
export interface SyncNowRequest {
  type: 'SYNC_NOW';
}
export interface CaptureEventsRequest {
  type: 'CAPTURE_EVENTS';
  events: UnboundEnvelope[];
}
export interface ContentPingRequest {
  type: 'CONTENT_PING';
}

export type PopupRequest = GetStatusRequest | SignInRequest | SignOutRequest | SetPausedRequest | SyncNowRequest;
export type ContentRequest = CaptureEventsRequest;

export interface StatusResponse {
  type: 'STATUS';
  version: string;
  /** ISO timestamp of when the current service worker instance started. */
  workerStartedAt: string;
  configured: boolean;
  signedIn: boolean;
  email: string | null;
  paused: boolean;
  /** Signed in and not paused. */
  captureEnabled: boolean;
  queued: number;
  quarantined: number;
  lastSync: LastSync | null;
  lastSuccessAt: number | null;
  backoffUntil: number;
  webUrl: string | null;
}

export interface ActionResponse {
  ok: boolean;
  error?: string;
}

export interface ContentStatusResponse {
  type: 'CONTENT_STATUS';
  provider: 'chatgpt';
  state: CaptureState;
  conversationDetected: boolean;
  /** Whether the adapter recognises the rendered conversation (content/captureStatus.ts). */
  capture: CaptureStatus;
  /** Messages the adapter parses on the page right now (final or still streaming). */
  visibleMessages: number;
}

/** chrome.storage.local keys readable by content scripts (booleans only, no secrets). */
export const STORAGE_KEYS = { captureEnabled: 'captureEnabled', paused: 'paused' } as const;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;

export function isPopupRequest(value: unknown): value is PopupRequest {
  if (!isObject(value)) return false;
  switch (value.type) {
    case 'GET_STATUS':
    case 'SIGN_OUT':
    case 'SYNC_NOW':
      return true;
    case 'SIGN_IN':
      return typeof value.email === 'string' && typeof value.password === 'string' && value.email.length <= 320 && value.password.length <= 1024;
    case 'SET_PAUSED':
      return typeof value.paused === 'boolean';
    default:
      return false;
  }
}

const nullableString = (v: unknown) => v === null || typeof v === 'string';
const nullableInt = (v: unknown) => v === null || Number.isInteger(v);

/** Structural check only; the backend performs full validation. */
export function isUnboundEnvelope(value: unknown): value is UnboundEnvelope {
  if (!isObject(value)) return false;
  return (
    typeof value.event_id === 'string' &&
    value.schema_version === 1 &&
    !('learner_id' in value) &&
    value.source_provider === 'chatgpt' &&
    value.source_method === 'browser_extension' &&
    nullableString(value.external_conversation_id) &&
    nullableString(value.external_message_id) &&
    nullableString(value.external_parent_message_id) &&
    nullableInt(value.message_index) &&
    (value.role === 'user' || value.role === 'assistant') &&
    typeof value.content_text === 'string' &&
    (value.content_format === 'text' || value.content_format === 'markdown') &&
    nullableString(value.occurred_at) &&
    typeof value.captured_at === 'string' &&
    nullableString(value.provider_model) &&
    Number.isInteger(value.revision_index) &&
    Array.isArray(value.attachment_metadata) &&
    typeof value.context_incomplete === 'boolean' &&
    value.active_course_id === null &&
    typeof value.content_hash === 'string' &&
    typeof value.client_event_id === 'string'
  );
}

export function isContentRequest(value: unknown): value is ContentRequest {
  return (
    isObject(value) &&
    value.type === 'CAPTURE_EVENTS' &&
    Array.isArray(value.events) &&
    value.events.length > 0 &&
    value.events.length <= 200 &&
    value.events.every(isUnboundEnvelope)
  );
}
