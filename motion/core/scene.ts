/* Scene v2 evaluation (S5, Docs/motion-scene-v2.md): composition + frame -> display list. Pure: no DOM, no
 * clock, no stored state, so preview, export and any later frame give the same answer for the same input.
 * The renderer (motion/render2.js) draws the list; it never decides timing itself. */
import { validateFps, type RationalFps } from './frame';
import { cubicBezier, easing as named, interpolate, interpolateColors, type Easing } from './math';
import { measureSpring, spring, type SpringOptions } from './spring';

export type EasingSpec = string | { bezier: [number, number, number, number] } | { spring: Pick<SpringOptions, 'mass' | 'stiffness' | 'damping'> };
export type Bound<T> = T | { prop: string };
export interface Keyframe { frame: number; value: Bound<number | string>; easing?: EasingSpec }
export interface SceneLayer {
  id: string; type: string; hidden?: boolean;
  keyframes?: Record<string, Keyframe[]>;
  layers?: SceneLayer[];
  [field: string]: unknown;
}
export interface Transition { kind: 'cut' | 'fade' | 'slide' | 'wipe' | 'zoom'; duration: number; easing?: EasingSpec; direction?: 'left' | 'right' | 'up' | 'down' }
export interface SceneClip { id: string; from: number; duration: number; offset?: number; transition?: Transition; layers: SceneLayer[] }
export interface SceneTrack { id: string; kind: 'visual' | 'audio'; hidden?: boolean; muted?: boolean; clips: SceneClip[] }
export interface Composition {
  id: string; width: number; height: number; fps: RationalFps; duration: number;
  background?: string; seed?: number; props?: Record<string, unknown>; tracks: SceneTrack[];
}

/** One visible layer at this frame. `values` holds every field of the layer resolved for this frame
 * (keyframes sampled, prop bindings looked up); defaults are the renderer's. */
export interface DrawLayer { id: string; type: string; values: Record<string, unknown>; children?: DrawLayer[] }
export interface DrawClip {
  id: string; track: string;
  /** Clip-local frame: keyframes and caption cues count from here. */
  frame: number;
  /** Content frame (local + offset): embedded media such as a room scene or a video plays from here. */
  contentFrame: number;
  duration: number;
  /** Entering transition while it runs: progress 0 (not yet in) to 1 (fully in), already eased. */
  transition: TransitionState | null;
  /** The next clip on this track entering over this one: the same progress, which this clip answers with the
   * complementary effect (fades out, is pushed out, is wiped away), so transitions work with transparent clips. */
  exit: TransitionState | null;
  layers: DrawLayer[];
}
export interface TransitionState { kind: Transition['kind']; progress: number; direction: NonNullable<Transition['direction']> }
export interface DisplayList { frame: number; width: number; height: number; background: string; clips: DrawClip[] }

const META = new Set(['id', 'type', 'name', 'hidden', 'keyframes', 'layers']);
const isProp = (v: unknown): v is { prop: string } => !!v && typeof v === 'object' && typeof (v as { prop?: unknown }).prop === 'string';

/** The easing of one keyframe segment `length` frames long. A spring is time-stretched so it settles
 * exactly at the next keyframe (overshoot is kept), which is what makes a spring keyframe editable. */
export function segmentEasing(spec: EasingSpec | undefined, fps: RationalFps, length: number): Easing {
  if (spec === undefined) return named.linear;
  if (typeof spec === 'string') {
    const curve = (named as Record<string, unknown>)[spec];
    if (typeof curve !== 'function' || spec === 'cubicBezier') throw new RangeError(`Unknown easing "${spec}"`);
    return curve as Easing;
  }
  if ('bezier' in spec) return cubicBezier(...spec.bezier);
  const options = { ...spec.spring, from: 0, to: 1 };
  const settle = measureSpring(fps, options);
  if (!Number.isFinite(settle) || settle <= 0 || length <= 0) return named.linear;
  // Frame f of the segment samples the spring at f * settle / length of its own frames.
  const stretched = { num: fps.num * length, den: fps.den * settle };
  return progress => progress >= 1 ? 1 : progress <= 0 ? 0 : spring(Math.round(progress * length), stretched, options);
}

/** A layer field at a clip-local frame: keyframes when it has them, else its static value. */
export function sampleField(layer: SceneLayer, name: string, frame: number, props: Readonly<Record<string, unknown>>, fps: RationalFps): unknown {
  const bound = (v: unknown) => isProp(v) ? props[v.prop] : v;
  const points = layer.keyframes?.[name];
  if (!points?.length) return bound(layer[name]);
  if (frame <= points[0].frame) return bound(points[0].value);
  const last = points[points.length - 1];
  if (frame >= last.frame) return bound(last.value);
  let i = 1;
  while (points[i].frame <= frame) i++;
  const a = bound(points[i - 1].value), b = bound(points[i].value);
  const range = [points[i - 1].frame, points[i].frame];
  const ease = segmentEasing(points[i].easing, fps, range[1] - range[0]);
  if (typeof a === 'number' && typeof b === 'number') return interpolate(frame, range, [a, b], { easing: ease });
  if (typeof a === 'string' && typeof b === 'string' && a.startsWith('#') && b.startsWith('#')) return interpolateColors(frame, range, [a, b], { easing: ease });
  // Anything else (text, enums) switches at the next keyframe.
  return a;
}

function drawLayers(layers: readonly SceneLayer[], frame: number, props: Readonly<Record<string, unknown>>, fps: RationalFps): DrawLayer[] {
  const out: DrawLayer[] = [];
  for (const layer of layers) {
    if (layer.hidden) continue;
    const values: Record<string, unknown> = {};
    const names = new Set([...Object.keys(layer).filter(k => !META.has(k)), ...Object.keys(layer.keyframes ?? {})]);
    for (const name of names) values[name] = sampleField(layer, name, frame, props, fps);
    const item: DrawLayer = { id: layer.id, type: layer.type, values };
    if (layer.type === 'group') item.children = drawLayers(layer.layers ?? [], frame, props, fps);
    out.push(item);
  }
  return out;
}

/** Everything visible at `frame`, bottom to top: tracks in order (later tracks draw above), and within a
 * track clips in start order, so a clip entering with a transition draws over the one it replaces. */
export function evaluate(composition: Composition, frame: number, overrides: Readonly<Record<string, unknown>> = {}): DisplayList {
  validateFps(composition.fps);
  if (!Number.isSafeInteger(frame) || frame < 0 || frame >= composition.duration) throw new RangeError(`frame must be an integer in [0, ${composition.duration - 1}]`);
  const props = { ...(composition.props ?? {}), ...overrides };
  const entering = (clip: SceneClip): TransitionState | null => {
    const t = clip.transition, local = frame - clip.from;
    if (!t || t.kind === 'cut' || t.duration <= 0 || local < 0 || local >= t.duration) return null;
    return { kind: t.kind, direction: t.direction ?? 'left', progress: segmentEasing(t.easing ?? 'ease-in-out', composition.fps, t.duration)(local / t.duration) };
  };
  const clips: DrawClip[] = [];
  for (const track of composition.tracks) {
    if (track.hidden || track.kind !== 'visual') continue;
    const ordered = [...track.clips].sort((a, b) => a.from - b.from);
    ordered.forEach((clip, i) => {
      const local = frame - clip.from;
      if (local < 0 || local >= clip.duration) return;
      // Validation allows a later clip to overlap this one only by its own transition.
      const next = ordered.slice(i + 1).find(c => c.from < clip.from + clip.duration && frame >= c.from);
      clips.push({
        id: clip.id, track: track.id, frame: local, contentFrame: local + (clip.offset ?? 0), duration: clip.duration,
        transition: entering(clip), exit: next ? entering(next) : null,
        layers: drawLayers(clip.layers, local, props, composition.fps),
      });
    });
  }
  return { frame, width: composition.width, height: composition.height, background: composition.background ?? '#0c0e10', clips };
}
