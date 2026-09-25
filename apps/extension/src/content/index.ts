/**
 * Content script for chatgpt.com (the only page it is injected into).
 *
 * Runs the ChatGPTAdapter + CaptureManager and hands captured envelopes to the
 * service worker. It holds no credentials and stores nothing in the page's
 * storage. Capture follows the `captureEnabled` flag the worker publishes
 * (signed in and not paused).
 */
import { CaptureManager, type UnboundEnvelope } from '../capture/CaptureManager';
import {
  STORAGE_KEYS,
  type ActionResponse,
  type CaptureEventsRequest,
  type ContentStatusResponse,
} from '../shared/messages';
import { ChatGPTAdapter } from './adapters/ChatGPTAdapter';
import { captureStatus } from './captureStatus';

const adapter = new ChatGPTAdapter();

if (adapter.isSupportedPage(window.location.href)) {
  // When the open conversation last changed (ChatGPT navigates without reloading the page),
  // so a conversation that is still rendering is not reported as an unrecognised layout.
  let conversation = adapter.getConversationExternalId();
  let conversationSince = Date.now();

  const manager = new CaptureManager({
    adapter,
    sink: {
      async enqueue(events: UnboundEnvelope[]) {
        const request: CaptureEventsRequest = { type: 'CAPTURE_EVENTS', events };
        const response = await chrome.runtime.sendMessage<CaptureEventsRequest, ActionResponse>(request);
        return { ok: Boolean(response?.ok), reason: response?.error };
      },
    },
  });

  chrome.storage.local.get(STORAGE_KEYS.captureEnabled).then((stored) => {
    manager.start({ paused: stored[STORAGE_KEYS.captureEnabled] !== true });
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !(STORAGE_KEYS.captureEnabled in changes)) return;
    if (changes[STORAGE_KEYS.captureEnabled].newValue === true) manager.resume();
    else manager.pause();
  });

  chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || (message as { type?: unknown })?.type !== 'CONTENT_PING') return false;
    const current = adapter.getConversationExternalId();
    if (current !== conversation) {
      conversation = current;
      conversationSince = Date.now();
    }
    const conversationDetected = current !== null;
    const visibleMessages = adapter.scan().length;
    const response: ContentStatusResponse = {
      type: 'CONTENT_STATUS',
      provider: 'chatgpt',
      state: manager.state,
      conversationDetected,
      capture: captureStatus({
        conversationOpen: conversationDetected,
        parsedMessages: visibleMessages,
        pageAgeMs: Date.now() - conversationSince,
      }),
      visibleMessages,
    };
    sendResponse(response);
    return false;
  });
}
