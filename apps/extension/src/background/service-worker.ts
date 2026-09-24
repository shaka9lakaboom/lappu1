/**
 * SkillMirror Chrome Extension Background Service Worker (P0 Foundation)
 */

console.log('[SkillMirror Background Worker] Initialized');

chrome.runtime.onInstalled.addListener(() => {
  console.log('[SkillMirror Background Worker] Extension installed/updated');
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[SkillMirror Background Worker] Message received:', message);
  if (message.type === 'PING') {
    sendResponse({ status: 'PONG', timestamp: new Date().toISOString() });
  }
  return true;
});
