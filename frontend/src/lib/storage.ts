/* localStorage throws when storage is disabled or full (private windows, policy);
 * view preferences are optional, so reads fall back and writes are dropped. */

export function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* preference is simply not remembered */
  }
}
