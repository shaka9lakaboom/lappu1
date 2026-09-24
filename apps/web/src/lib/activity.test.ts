import { describe, expect, it } from 'vitest';

import { processingLabel, providerLabel, shortId, shortPreview } from './activity';

describe('activity helpers', () => {
  it('labels processing states without implying any skill judgement', () => {
    expect(processingLabel('PENDING')).toBe('Waiting for processing');
    expect(processingLabel('FAILED')).toBe('Processing failed');
    expect(processingLabel(null)).toBe('Not queued');
  });

  it('labels providers', () => {
    expect(providerLabel('chatgpt')).toBe('ChatGPT');
  });

  it('shortens previews and marks truncation', () => {
    expect(shortPreview('Explain binary search.', 22)).toBe('Explain binary search.');
    expect(shortPreview('line one\n\n  line two', 20)).toBe('line one line two');
    expect(shortPreview('x'.repeat(280), 5000, 10)).toBe(`${'x'.repeat(10)}…`);
    expect(shortPreview('short', 900)).toBe('short…');
  });

  it('keeps markup as plain text', () => {
    expect(shortPreview('<img src=x onerror=alert(1)>', 28)).toBe('<img src=x onerror=alert(1)>');
  });

  it('shortens ids', () => {
    expect(shortId('6ab58b53-14f8-83ea-853a-57c0ab951696')).toBe('6ab58b53');
    expect(shortId(null)).toBe('—');
  });
});
