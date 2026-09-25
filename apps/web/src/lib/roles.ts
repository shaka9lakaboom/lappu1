/**
 * Teacher / admin areas (P7, ADR 0008). The backend is the only authority on roles: it reads the
 * profile role from the database for every request. The web app uses GET /v1/me only to decide
 * which links to show, and renders a 403 from any teacher / admin endpoint as a plain panel.
 */
import type { MeResponse } from '@skillmirror/contracts';

import { ApiError, apiRequest } from './api';

export interface AreaLink {
  href: string;
  label: string;
}

export function areaLinks(me: Pick<MeResponse, 'capabilities'> | null): AreaLink[] {
  if (!me) return [];
  const links: AreaLink[] = [];
  if (me.capabilities.teacher) links.push({ href: '/teacher', label: 'Teaching' });
  if (me.capabilities.admin) links.push({ href: '/admin', label: 'Admin' });
  return links;
}

export type Loaded<T> =
  | { state: 'ok'; data: T }
  | { state: 'forbidden'; message: string }
  | { state: 'not_found' }
  | { state: 'error'; message: string };

/** GET a teacher / admin resource; 403 and 404 become states, other API errors a message. */
export async function loadArea<T>(
  path: string,
  accessToken: string,
  request: typeof apiRequest = apiRequest,
): Promise<Loaded<T>> {
  try {
    return { state: 'ok', data: await request<T>(path, accessToken) };
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 403) return { state: 'forbidden', message: error.message };
    if (error.status === 404) return { state: 'not_found' };
    return { state: 'error', message: error.message };
  }
}
