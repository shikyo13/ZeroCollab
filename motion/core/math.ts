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
  cubicBezier,
});

export interface InterpolateOptions {
  extrapolate?: Extrapolation;
  extrapolateLeft?: Extrapolation;
  extrapolateRight?: Extrapolation;
  easing?: Easing;
}

export function interpolate(value: number, input: readonly number[], output: readonly number[], options: InterpolateOptions = {}): number {
  finite(value, 'value');
  if (input.length < 2 || input.length !== output.length) throw new RangeError('Interpolation needs equal ranges of at least two values');
  input.forEach((v, i) => {
    finite(v, `input[${i}]`); finite(output[i], `output[${i}]`);
    if (i && v <= input[i - 1]) throw new RangeError('Input range must be strictly increasing');
  });
  const left = options.extrapolateLeft ?? options.extrapolate ?? 'clamp';
  const right = options.extrapolateRight ?? options.extrapolate ?? 'clamp';
  if (![left, right].every(v => v === 'clamp' || v === 'extend')) throw new RangeError('Unknown extrapolation');
  if (value < input[0] && left === 'clamp') return output[0];
  const end = input.length - 1;
  if (value > input[end] && right === 'clamp') return output[end];
  let segment = 0;
  while (segment < end - 1 && value >= input[segment + 1]) segment++;
  const progress = (value - input[segment]) / (input[segment + 1] - input[segment]);
  // Endpoint segments extend linearly; a bounded easing curve must not silently clamp them.
  const shaped = progress < 0 || progress > 1 ? progress : (options.easing ?? easing.linear)(progress);
  return output[segment] + (output[segment + 1] - output[segment]) * finite(shaped, 'easing result');
}

type Color = readonly [number, number, number, number];
function color(value: string): Color {
  const hex = /^#([\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i.exec(value.trim());
  if (hex) {
    let s = hex[1];
    if (s.length < 5) s = [...s].map(c => c + c).join('');
    return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16), s.length === 8 ? parseInt(s.slice(6, 8), 16) / 255 : 1];
  }
  const rgb = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i.exec(value.trim());
  if (!rgb) throw new RangeError('Colors must be hex, rgb() or rgba() values');
  const channels = [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] === undefined ? 1 : Number(rgb[4])];
  if (channels.some((v, i) => !Number.isFinite(v) || v < 0 || v > (i === 3 ? 1 : 255))) throw new RangeError('Color channel out of range');
  return channels as unknown as Color;
}

/** Straight sRGB channel interpolation, with explicitly preserved alpha. */
export function interpolateColors(value: number, input: readonly number[], output: readonly string[], options: InterpolateOptions = {}): string {
  const colors = output.map(color);
  const channels = [0, 1, 2, 3].map(channel => interpolate(value, input, colors.map(c => c[channel]), options));
  return `rgba(${channels.slice(0, 3).map(v => Math.round(clamp(v, 0, 255))).join(', ')}, ${Number(clamp(channels[3]).toFixed(6))})`;
}
