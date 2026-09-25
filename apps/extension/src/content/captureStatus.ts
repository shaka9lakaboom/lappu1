/**
 * Whether the content script can actually read the open ChatGPT page. "Tracking: On" only says
 * capture is enabled; a provider layout change can leave it enabled while nothing is recognised,
 * which must not look healthy.
 */
export type CaptureStatus = 'ok' | 'no_conversation' | 'waiting' | 'layout_unrecognized';

/** A conversation page gets this long to render its messages before it counts as unrecognised. */
export const RENDER_GRACE_MS = 8000;

export function captureStatus({
  conversationOpen,
  parsedMessages,
  pageAgeMs,
}: {
  conversationOpen: boolean;
  parsedMessages: number;
  pageAgeMs: number;
}): CaptureStatus {
  if (parsedMessages > 0) return 'ok';
  if (!conversationOpen) return 'no_conversation';
  return pageAgeMs < RENDER_GRACE_MS ? 'waiting' : 'layout_unrecognized';
}

export function captureStatusLabel(status: CaptureStatus, visibleMessages: number): string {
  switch (status) {
    case 'ok':
      return `Capturing (${visibleMessages} message${visibleMessages === 1 ? '' : 's'} visible)`;
    case 'no_conversation':
      return 'No conversation open';
    case 'waiting':
      return 'Reading the conversation…';
    case 'layout_unrecognized':
      return 'ChatGPT layout not recognized';
  }
}
