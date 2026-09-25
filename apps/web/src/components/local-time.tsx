'use client';

import { useSyncExternalStore } from 'react';

/** "14:55 UTC": what the server renders, since it does not know the viewer's time zone. */
export function utcTimeLabel(iso: string): string {
  return `${new Date(iso).toISOString().slice(11, 16)} UTC`;
}

function localTimeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
}

const noSubscription = () => () => {};

/** A time of day in the viewer's own time zone (UTC on the server and during hydration). */
export function LocalTime({ iso }: { iso: string }) {
  const label = useSyncExternalStore(
    noSubscription,
    () => localTimeLabel(iso),
    () => utcTimeLabel(iso),
  );
  return (
    <time dateTime={iso} title={new Date(iso).toISOString()}>
      {label}
    </time>
  );
}
