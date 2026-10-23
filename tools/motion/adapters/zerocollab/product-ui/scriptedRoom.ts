/** Build-only replacement of the room bridge. UI surfaces never attach Hub,
 * fetch project state, send messages or read wall time. */
const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;
const SECONDS_PER_DAY = 86400;

export function sessionTitle(id: string): string { return id; }
export function since(timestamp: number, now = 0): string {
  const seconds = Math.max(0, Math.floor(now - timestamp));
  if (seconds < SECONDS_PER_MINUTE) return 'just now';
  if (seconds < SECONDS_PER_HOUR) return `${Math.floor(seconds / SECONDS_PER_MINUTE)}m ago`;
  if (seconds < SECONDS_PER_DAY) return `${Math.floor(seconds / SECONDS_PER_HOUR)}h ago`;
  return `${Math.floor(seconds / SECONDS_PER_DAY)}d ago`;
}
