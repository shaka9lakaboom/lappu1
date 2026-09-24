/** Runtime messages between extension contexts (popup <-> service worker). */

export interface GetStatusRequest {
  type: 'GET_STATUS';
}

export interface StatusResponse {
  type: 'STATUS';
  version: string;
  /** ISO timestamp of when the current service worker instance started. */
  workerStartedAt: string;
  /** Capture is not implemented until P1; never report it as active. */
  capture: 'not_available';
}

export type ExtensionRequest = GetStatusRequest;

export function isExtensionRequest(value: unknown): value is ExtensionRequest {
  return typeof value === 'object' && value !== null && (value as { type?: unknown }).type === 'GET_STATUS';
}
