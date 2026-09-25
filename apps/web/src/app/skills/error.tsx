'use client';

import { RouteError } from '@/components/experience/route-error';

export default function RouteErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError title="Skill map" error={error} retry={retry} />;
}
