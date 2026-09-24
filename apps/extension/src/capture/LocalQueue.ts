/**
 * LocalQueue storage abstraction shell (P0)
 */
export class LocalQueue<T> {
  private queue: T[] = [];

  enqueue(item: T): void {
    this.queue.push(item);
  }

  dequeue(): T | undefined {
    return this.queue.shift();
  }

  size(): number {
    return this.queue.length;
  }

  clear(): void {
    this.queue = [];
  }
}
