import { describe, expect, it } from 'vitest';

import { safeRedirectPath } from './redirect';

describe('safeRedirectPath', () => {
  it.each(['/dashboard', '/dashboard?tab=1'])('keeps same-origin path %s', (path) => {
    expect(safeRedirectPath(path)).toBe(path);
  });

  it.each(['https://evil.example', '//evil.example', '/\\evil.example', 'dashboard', '', null, undefined])(
    'falls back to /dashboard for %s',
    (value) => {
      expect(safeRedirectPath(value)).toBe('/dashboard');
    },
  );
});
