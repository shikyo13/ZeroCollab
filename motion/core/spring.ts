import { frameToMilliseconds, validateFps, type RationalFps } from './frame';
import { clamp, finite } from './math';

export interface SpringOptions {
  mass?: number; stiffness?: number; damping?: number;
  from?: number; to?: number; velocity?: number; overshootClamping?: boolean;
}

function criticalState(displacement: number, velocity: number, seconds: number, omega: number): [number, number] {
  const e = Math.exp(-omega * seconds), step = (velocity + omega * displacement) * seconds;
  return [(displacement + step) * e, (velocity - omega * step) * e];
}

/** State-based realtime variant for Live. Position/velocity are exact for any nonnegative dt. */
export function springStep(x: number, velocity: number, target: number, dt: number, omega = 9): [number, number] {
  [x, velocity, target, dt, omega].forEach((v, i) => finite(v, `spring step ${i}`));
  if (dt < 0 || omega <= 0) throw new RangeError('Spring dt must be nonnegative and omega positive');
  const [position, nextVelocity] = criticalState(x - target, velocity, dt, omega);
  return [target + position, nextVelocity];
}

function model(options: SpringOptions) {
  const mass = options.mass ?? 1, stiffness = options.stiffness ?? 120, damping = options.damping ?? 20;
  [mass, stiffness, damping, options.from ?? 0, options.to ?? 1, options.velocity ?? 0].forEach((v, i) => finite(v, `spring input ${i}`));
  if (mass <= 0 || stiffness <= 0 || damping < 0) throw new RangeError('Spring mass/stiffness must be positive and damping nonnegative');
  const from = options.from ?? 0, to = options.to ?? 1, velocity = options.velocity ?? 0;
  const a = damping / (2 * mass), omega2 = stiffness / mass, discriminant = a * a - omega2, initial = from - to;
  const critical = Math.abs(discriminant) <= Number.EPSILON * 16 * Math.max(a * a, omega2);
  if (critical) {
    const b = velocity + a * initial;
    return { from, to, rest: initial === 0 && velocity === 0, a, monotonicAfter: a ? 1 / a : 0,
      value: (t: number) => criticalState(initial, velocity, t, a)[0],
      envelope: (t: number) => [Math.exp(-a * t) * (Math.abs(initial) + Math.abs(b) * t), Math.exp(-a * t) * (Math.abs(velocity) + a * Math.abs(b) * t)] };
  }
  if (discriminant < 0) {
    const w = Math.sqrt(-discriminant), b = (velocity + a * initial) / w;
    const positionBound = Math.hypot(initial, b), velocityBound = Math.hypot(-a * initial + w * b, -a * b - w * initial);
    return { from, to, rest: initial === 0 && velocity === 0, a, monotonicAfter: 0,
      value: (t: number) => Math.exp(-a * t) * (initial * Math.cos(w * t) + b * Math.sin(w * t)),
      envelope: (t: number) => [positionBound * Math.exp(-a * t), velocityBound * Math.exp(-a * t)] };
  }
  const q = Math.sqrt(discriminant), r1 = -omega2 / (a + q), r2 = -a - q;
  const first = (velocity - r2 * initial) / (r1 - r2), second = initial - first;
  return { from, to, rest: initial === 0 && velocity === 0, a, monotonicAfter: 0,
    value: (t: number) => first * Math.exp(r1 * t) + second * Math.exp(r2 * t),
    envelope: (t: number) => [Math.abs(first) * Math.exp(r1 * t) + Math.abs(second) * Math.exp(r2 * t), Math.abs(r1 * first) * Math.exp(r1 * t) + Math.abs(r2 * second) * Math.exp(r2 * t)] };
}

/** Closed-form damped oscillator. Negative frames hold the exact initial value. Velocity is units/s. */
export function spring(frame: number, fps: RationalFps, options: SpringOptions = {}): number {
  const m = model(options), seconds = Math.max(0, frameToMilliseconds(frame, fps)) / 1000;
  const value = seconds === 0 ? m.from : m.to + m.value(seconds);
  return options.overshootClamping ? clamp(value, Math.min(m.from, m.to), Math.max(m.from, m.to)) : value;
}

/** Conservative settle duration: displacement AND velocity remain under the thresholds thereafter.
 * An undamped moving spring has no finite duration. This never samples or steps a simulation. */
export function measureSpring(fps: RationalFps, options: SpringOptions & { threshold?: number; velocityThreshold?: number } = {}): number {
  validateFps(fps); const m = model(options);
  const threshold = options.threshold ?? .001, velocityThreshold = options.velocityThreshold ?? .001;
  if (!(finite(threshold, 'threshold') > 0) || !(finite(velocityThreshold, 'velocityThreshold') > 0)) throw new RangeError('Settle thresholds must be positive');
  if (m.rest) return 0;
  if (m.a === 0) return Infinity;
  const settled = (t: number) => { const [position, velocity] = m.envelope(t); return position <= threshold && velocity <= velocityThreshold; };
  let low = m.monotonicAfter, high = Math.max(1, low);
  const maximum = Number.MAX_SAFE_INTEGER * fps.den / fps.num;
  while (!settled(high)) {
    high *= 2;
    if (!Number.isFinite(high) || high > maximum) return Infinity;
  }
  if (!settled(low)) for (let i = 0; i < 64; i++) {
    const mid = (low + high) / 2;
    if (settled(mid)) high = mid; else low = mid;
  } else high = low;
  return Math.ceil(high * fps.num / fps.den);
}
