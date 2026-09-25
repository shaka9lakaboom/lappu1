'use client';

import { RouteError } from '@/components/experience/route-error';

export default function RouteErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError title="Activity" error={error} retry={retry} />;
}
