const DEFAULT_AFTER_SIGN_IN = '/dashboard';

/**
 * Only same-origin absolute paths are allowed as post-auth redirects, so a
 * crafted `?next=` cannot send a freshly signed-in user to another site.
 */
export function safeRedirectPath(value: unknown): string {
  if (typeof value !== 'string') return DEFAULT_AFTER_SIGN_IN;
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return DEFAULT_AFTER_SIGN_IN;
  return value;
}
