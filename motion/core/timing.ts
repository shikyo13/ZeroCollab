import { clamp, integer } from './math';

export interface SequenceOptions { from: number; duration: number; offset?: number }
export interface TimingSample { active: boolean; frame: number; progress: number }

/** Half-open visibility [from, from+duration); progress spans the first through last visible frame. */
export function sequence(frame: number, options: SequenceOptions): TimingSample {
  integer(frame, 'frame'); integer(options.from, 'from'); integer(options.duration, 'duration', 1);
  const local = frame - options.from;
  return { active: local >= 0 && local < options.duration, frame: local + integer(options.offset ?? 0, 'offset'), progress: options.duration === 1 ? (local < 0 ? 0 : 1) : clamp(local / (options.duration - 1)) };
}

export function series(frame: number, clips: readonly { duration: number; offset?: number }[], from = 0): TimingSample[] {
  integer(from, 'from');
  let start = from;
  return clips.map(clip => {
    const sample = sequence(frame, { ...clip, from: start });
    start += clip.duration; integer(start, 'series end');
    return sample;
  });
}

export interface LoopOptions { duration: number; from?: number; count?: number; pingPong?: boolean }
export function loop(frame: number, options: LoopOptions): TimingSample & { iteration: number; direction: 1 | -1 } {
  integer(frame, 'frame'); integer(options.duration, 'duration', 1);
  const from = integer(options.from ?? 0, 'from');
  const count = options.count === undefined ? Infinity : integer(options.count, 'count', 1);
  const period = options.pingPong && options.duration > 1 ? 2 * (options.duration - 1) : options.duration;
  const relative = frame - from, iteration = Math.floor(relative / period);
  const phase = ((relative % period) + period) % period;
  const backwards = !!options.pingPong && phase >= options.duration;
  const local = backwards ? period - phase : phase;
  return { active: relative >= 0 && iteration < count, frame: local, progress: options.duration === 1 ? 1 : local / (options.duration - 1), iteration, direction: backwards ? -1 : 1 };
}

/** A bounded hold leaves the outside timeline unchanged. Omit duration for a permanent hold. */
export function freeze(frame: number, options: { at: number; from?: number; duration?: number }): number {
  integer(frame, 'frame'); integer(options.at, 'at');
  const from = integer(options.from ?? 0, 'from');
  const duration = options.duration === undefined ? Infinity : integer(options.duration, 'duration', 1);
  return frame >= from && frame < from + duration ? options.at : frame;
}

export function stagger(frame: number, index: number, delay: number): number {
  integer(frame, 'frame'); integer(index, 'index', 0); integer(delay, 'delay', 0);
  return integer(frame - index * delay, 'staggered frame');
}
