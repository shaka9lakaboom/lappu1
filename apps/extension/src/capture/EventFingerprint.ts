/**
 * EventFingerprint generator foundation (P0)
 */
export function generateEventFingerprint(text: string, timestamp: string): string {
  let hash = 0;
  const str = `${text}_${timestamp}`;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return `fp_${Math.abs(hash).toString(16)}`;
}
