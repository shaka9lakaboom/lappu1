/**
 * SkillMirror Companion service worker.
 *
 * P0 only answers status requests. P1 adds authentication/session state,
 * the LocalQueue flush loop and API communication here.
 */
import { isExtensionRequest, type StatusResponse } from '../shared/messages';

const workerStartedAt = new Date().toISOString();

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  // Only accept messages from this extension's own pages.
  if (sender.id !== chrome.runtime.id || !isExtensionRequest(message)) return false;

  const response: StatusResponse = {
    type: 'STATUS',
    version: chrome.runtime.getManifest().version,
    workerStartedAt,
    capture: 'not_available',
  };
  sendResponse(response);
  return false;
});
