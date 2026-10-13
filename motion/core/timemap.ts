/* Source time for recorded footage (28cb5faf, Zero's storyboard: "accelerated" build beats). A video's `speed` can
 * be keyframed (a speed ramp); source time is then the running sum of speed over the clip's content frames, so the
 * picture, the recorded cursor and the follow camera all read one clock. Computed once per layer and cached. */
import type { RationalFps } from './frame';
import { sampleField, type SceneLayer } from './scene';
import type { Props } from './memo';

export interface TimeMap {
  /** Source seconds at a content frame (trim included); fractional frames interpolate. */
  seconds(contentFrame: number): number;
  /** The earliest content frame showing a source time (for mapping recorded events onto the timeline). */
  frameAt(seconds: number): number;
}

// Per layer, then per props object (the caller's merged props: compositionProps), then per length and fps.
const cache = new WeakMap<SceneLayer, WeakMap<Props, Map<string, TimeMap>>>();
const number = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;

/** speed (keyframable, default `rate`, default 1) integrated over content frames 0..frames. */
export function timeMap(layer: SceneLayer, frames: number, props: Props, fps: RationalFps): TimeMap {
  const key = `${frames}|${fps.num}/${fps.den}`;
  const known = cache.get(layer)?.get(props)?.get(key);
  if (known) return known;
  const step = fps.den / fps.num, base = number(sampleField(layer, 'rate', 0, props, fps), 1);
  const trim = number(sampleField(layer, 'trim', 0, props, fps), 0) * step;
  const sums = new Float64Array(frames + 1);
  sums[0] = trim;
  for (let f = 0; f < frames; f++) {
    // Trapezoid over each frame, so a ramp's time is exact for piecewise-linear speed.
    const a = number(sampleField(layer, 'speed', f, props, fps), base), b = number(sampleField(layer, 'speed', f + 1, props, fps), base);
    sums[f + 1] = sums[f] + (Math.max(0, a) + Math.max(0, b)) / 2 * step;
  }
  const map: TimeMap = {
    seconds(contentFrame) {
      if (contentFrame <= 0) return sums[0] + contentFrame * base * step;
      if (contentFrame >= frames) return sums[frames] + (contentFrame - frames) * base * step;
      const i = Math.floor(contentFrame), u = contentFrame - i;
      return sums[i] + (sums[i + 1] - sums[i]) * u;
    },
    frameAt(seconds) {
      if (seconds <= sums[0]) return 0;
      if (seconds >= sums[frames]) return frames;
      let lo = 0, hi = frames;
      // sums[lo] < seconds <= sums[hi]: on a freeze (speed 0) the earliest frame showing that time wins.
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (sums[mid] < seconds) lo = mid; else hi = mid; }
      const span = sums[hi] - sums[lo];
      return lo + (span > 0 ? (seconds - sums[lo]) / span : 0);
    },
  };
  let byProps = cache.get(layer);
  if (!byProps) cache.set(layer, byProps = new WeakMap());
  let byKey = byProps.get(props);
  if (!byKey) byProps.set(props, byKey = new Map());
  byKey.set(key, map);
  return map;
}
