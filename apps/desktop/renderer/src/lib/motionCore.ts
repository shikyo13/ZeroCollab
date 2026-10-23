// Desktop presentation primitives; independent of the optional production renderer.
export type Easing = (progress: number) => number;
export type Extrapolation = 'clamp' | 'extend';

export function finite(value: number, name: string): number {
  if (!Number.isFinite(value)) throw new RangeError(`${name} must be finite`);
  return value;
}

export function integer(value: number, name: string, minimum = -Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || value < minimum) throw new RangeError(`${name} must be an integer >= ${minimum}`);
  return value;
}

export const clamp = (value: number, low = 0, high = 1): number => Math.max(low, Math.min(high, value));

/** Solves x(u) for a cubic curve, then evaluates y(u). There is no clock or stored phase. */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): Easing {
  [x1, y1, x2, y2].forEach((v, i) => finite(v, `bezier control ${i}`));
  if (x1 < 0 || x1 > 1 || x2 < 0 || x2 > 1) throw new RangeError('Bezier x controls must be in [0, 1]');
  const axis = (u: number, a: number, b: number) => 3 * (1 - u) ** 2 * u * a + 3 * (1 - u) * u * u * b + u ** 3;
  return progress => {
    finite(progress, 'progress');
    if (progress <= 0) return 0;
    if (progress >= 1) return 1;
    let low = 0, high = 1;
    for (let i = 0; i < 48; i++) {
      const u = (low + high) / 2;
      if (axis(u, x1, x2) < progress) low = u; else high = u;
    }
    return axis((low + high) / 2, y1, y2);
  };
}

export const easing = Object.freeze({
  linear: ((p: number) => p) as Easing,
  ease: cubicBezier(.25, .1, .25, 1),
  easeIn: cubicBezier(.42, 0, 1, 1),
  easeOut: cubicBezier(0, 0, .58, 1),
  easeInOut: cubicBezier(.42, 0, .58, 1),
  'ease-in': cubicBezier(.42, 0, 1, 1),
  'ease-out': cubicBezier(0, 0, .58, 1),
  'ease-in-out': cubicBezier(.42, 0, .58, 1),
  'step-start': ((p: number) => p < 0 ? 0 : 1) as Easing,
  'step-end': ((p: number) => p < 1 ? 0 : 1) as Easing,
  // Demo presets (28cb5faf): camera and title moves that start decisively and land softly.
  'expo-out': cubicBezier(.16, 1, .3, 1),
  'expo-in-out': cubicBezier(.87, 0, .13, 1),
  premium: cubicBezier(.65, 0, .35, 1),
  cubicBezier,
});
