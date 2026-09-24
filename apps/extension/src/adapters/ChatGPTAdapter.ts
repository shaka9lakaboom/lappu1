import { ProviderAdapter } from './ProviderAdapter';

/**
 * ChatGPTAdapter shell (P0)
 * Note: DOM capture logic is deferred to P1.
 */
export class ChatGPTAdapter implements ProviderAdapter {
  name = 'ChatGPT';

  isMatchingUrl(url: string): boolean {
    return url.includes('chatgpt.com') || url.includes('chat.openai.com');
  }

  initialize(): void {
    console.log('[ChatGPTAdapter] Initialized shell');
  }

  cleanup(): void {
    console.log('[ChatGPTAdapter] Cleaned up');
  }
}
