/**
 * ProviderAdapter interface foundation (P0)
 */

export interface ProviderAdapter {
  name: string;
  isMatchingUrl(url: string): boolean;
  initialize(): void;
  cleanup(): void;
}
