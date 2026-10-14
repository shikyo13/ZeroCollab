/** Build-only inert bridge. Real CardFollowLayer is retained, but importing it
 * must not run followStore's live attach(), storage or expiry timers. */
export function reconcileRuns(): void {}
export function setFollowing(): void {}
export function setPaused(): void {}
export function useFollow() {
  return { following: true, paused: false, live: {}, ticker: [], moves: {}, pip: null, pips: [] };
}
