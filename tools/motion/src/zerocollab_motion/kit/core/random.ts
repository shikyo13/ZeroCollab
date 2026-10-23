import { integer } from './math';

/** A stateless hash to [0, 1): the same (seed, key) gives the same number in preview, export and on any
 * machine, and no call order or stored state is involved. Keys name what is being randomised, for example
 * `${layer}:jitter:${frame}`, so adding a layer never shifts another layer's values. */
export function random(seed: number, key: string | number): number {
  integer(seed, 'seed', 0);
  if (seed > 0x7fffffff) throw new RangeError('seed must be in [0, 2^31-1]');
  // FNV-1a over the key's UTF-16 code units, then a murmur3 finaliser mixed with the seed.
  let h = 0x811c9dc5;
  const text = String(key);
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  h ^= Math.imul(seed ^ 0x9e3779b9, 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** Smooth 1D value noise in [-1, 1] at position t (for example a frame divided by a period). Lattice
 * values come from random(), joined by a smoothstep, so motion is continuous and repeatable. */
export function noise(seed: number, key: string | number, t: number): number {
  const i = Math.floor(t), f = t - i, s = f * f * (3 - 2 * f);
  const a = random(seed, `${key}:${i}`) * 2 - 1, b = random(seed, `${key}:${i + 1}`) * 2 - 1;
  return a + (b - a) * s;
}
