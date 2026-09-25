import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { AppHeader } from '@/components/experience/app-header';
import { StateBar } from '@/components/teacher/state-bar';
import { TONE_CLASSES } from '@/lib/experience';

const html = (node: React.ReactElement) => renderToStaticMarkup(node);

describe('class state bar', () => {
  const markup = html(
    <StateBar
      label="Skill states across the class"
      states={{ UNKNOWN: 6, EMERGING: 3, DEVELOPING: 3, DEMONSTRATED: 3, VERIFIED: 0, NEEDS_REVERIFICATION: 0 }}
    />,
  );

  it('writes every count out and shows UNKNOWN in the neutral tone', () => {
    expect(markup).toContain('Not enough evidence yet: <span class="font-medium tabular-nums text-foreground">6</span>');
    expect(markup).toContain('Demonstrated: <span class="font-medium tabular-nums text-foreground">3</span>');
    expect(markup).toContain('Verified: <span class="font-medium tabular-nums text-foreground">0</span>');
    const unknownSegment = markup.match(/<div class="([^"]*)" style="width:40%" data-state="UNKNOWN"/);
    expect(unknownSegment?.[1]).toContain(TONE_CLASSES.neutral.split(' ')[0]);
  });

  it('never uses failure wording or colour', () => {
    expect(markup).not.toMatch(/weak|poor|fail/i);
    expect(markup).not.toContain('destructive');
    expect(markup).not.toContain('red-');
  });

  it('draws only the non-empty states', () => {
    expect(markup).not.toContain('data-state="VERIFIED" title');
    expect((markup.match(/ title="/g) ?? []).length).toBe(4);
  });
});

describe('areas', () => {
  it('shows teacher / admin links only when given', () => {
    expect(html(<AppHeader current="/dashboard" title="Dashboard" />)).not.toContain('area-link');
    const withAreas = html(
      <AppHeader current="/dashboard" title="Dashboard" areas={[{ href: '/admin', label: 'Admin' }]} />,
    );
    expect(withAreas).toContain('href="/admin"');
  });

  it('renders the admin navigation and the forbidden panel', () => {
    const header = html(<AreaHeader area="admin" current="/admin/jobs" title="Processing jobs" />);
    for (const href of ['/admin', '/admin/jobs', '/admin/model-runs', '/admin/skill-candidates', '/admin/benchmark']) {
      expect(header).toContain(`href="${href}"`);
    }
    expect(header).toContain('aria-current="page"');
    expect(html(<ForbiddenPanel area="admin" />)).toContain('This area is for SkillMirror administrators.');
    expect(html(<ForbiddenPanel area="teacher" />)).toContain('This area is for teachers.');
  });
});
