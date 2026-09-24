import { LocalQueue } from './LocalQueue';

/**
 * CaptureManager foundation (P0)
 */
export class CaptureManager {
  private queue = new LocalQueue<any>();
  private active = false;

  start(): void {
    this.active = true;
    console.log('[CaptureManager] Capture started (P0 Shell)');
  }

  stop(): void {
    this.active = false;
    console.log('[CaptureManager] Capture stopped');
  }

  isActive(): boolean {
    return this.active;
  }
}
