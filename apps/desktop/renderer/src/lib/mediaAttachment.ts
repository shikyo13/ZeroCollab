/** Only local uploaded media becomes an inline player. Never fetch arbitrary message URLs. */
export function uploadMedia(value: string): string | undefined {
  try {
    const url = new URL(value, location.href);
    return url.origin === location.origin && /^\/uploads\/[a-z0-9_-][a-z0-9_.-]*\.(png|jpe?g|gif|webp|webm|mp4)$/i.test(url.pathname) ? url.pathname : undefined;
  } catch { return undefined; }
}
export const isVideoAttachment = (value: string): boolean => /\.(webm|mp4)$/i.test(uploadMedia(value) || '');
