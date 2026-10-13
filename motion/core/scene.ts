/* Scene v2 evaluation (S5, Docs/motion-scene-v2.md): composition + frame -> display list. Pure: no DOM, no
 * clock, no stored state, so preview, export and any later frame give the same answer for the same input.
 * The renderer (motion/render2.js) draws the list; it never decides timing itself. */
import { validateFps, type RationalFps } from './frame';
import { cubicBezier, easing as named, interpolate, interpolateColors, type Easing } from './math';
import { measureSpring, spring, type SpringOptions } from './spring';
import { blendViews, compose, IDENTITY, layerAffine, mapRegion, regionView, type Affine, type CameraView } from './camera';
import { followShots, type CursorEvent } from './cursor';
import { timeMap } from './timemap';
import { NO_PROPS, pairCache, type Props } from './memo';

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
/** fixed: the track ignores the camera (titles, lower-thirds and captions stay put while the shot moves). */
export interface SceneTrack { id: string; kind: 'visual' | 'audio'; hidden?: boolean; muted?: boolean; fixed?: boolean; clips: SceneClip[] }
/** One camera framing at a composition frame. A `region` [x, y, w, h] (in composition px, or in a layer's own box
 * px when `layer` is set) is framed with `padding`; explicit x, y, zoom and rotate override it. `easing` shapes
 * the move from the previous keyframe (default 'premium'). */
export interface CameraKey {
  frame: number; region?: [number, number, number, number]; layer?: string; padding?: number;
  x?: number; y?: number; zoom?: number; rotate?: number; easing?: EasingSpec;
}
export interface Composition {
  id: string; width: number; height: number; fps: RationalFps; duration: number;
  background?: string; seed?: number; props?: Record<string, unknown>; tracks: SceneTrack[];
  camera?: { keyframes?: CameraKey[]; follow?: { cursor: string; zoom?: number; idle_ms?: number } };
  /** Per-composition motion blur from camera velocity: shutter in degrees (180 is film-like). */
  motion_blur?: { shutter?: number };
  backdrop?: string; font_asset?: string;
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
  /** The clip's track ignores the camera. */
  fixed: boolean;
  layers: DrawLayer[];
}
export interface TransitionState { kind: Transition['kind']; progress: number; direction: NonNullable<Transition['direction']> }
export interface DisplayList {
  frame: number; width: number; height: number; background: string; clips: DrawClip[];
  /** The camera at this frame and at the previous one (for motion blur; never playback history). */
  camera: CameraView; cameraPrev: CameraView;
}

/** Named springs for camera and title moves: soft settles with a little give, snappy lands fast and firm. */
export const SPRING_PRESETS: Readonly<Record<string, Pick<SpringOptions, 'mass' | 'stiffness' | 'damping'>>> = {
  'spring-soft': { stiffness: 110, damping: 18 }, 'spring-snappy': { stiffness: 320, damping: 30 },
};

const META = new Set(['id', 'type', 'name', 'hidden', 'keyframes', 'layers']);
const isProp = (v: unknown): v is { prop: string } => !!v && typeof v === 'object' && typeof (v as { prop?: unknown }).prop === 'string';

/** The easing of one keyframe segment `length` frames long. A spring is time-stretched so it settles
 * exactly at the next keyframe (overshoot is kept), which is what makes a spring keyframe editable. */
export function segmentEasing(spec: EasingSpec | undefined, fps: RationalFps, length: number): Easing {
  if (spec === undefined) return named.linear;
  if (typeof spec === 'string' && spec in SPRING_PRESETS) return segmentEasing({ spring: SPRING_PRESETS[spec] }, fps, length);
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
export function evaluate(composition: Composition, frame: number, overrides: Props = NO_PROPS): DisplayList {
  validateFps(composition.fps);
  if (!Number.isSafeInteger(frame) || frame < 0 || frame >= composition.duration) throw new RangeError(`frame must be an integer in [0, ${composition.duration - 1}]`);
  const props = compositionProps(composition, overrides);
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
        transition: entering(clip), exit: next ? entering(next) : null, fixed: !!track.fixed,
        layers: drawLayers(clip.layers, local, props, composition.fps),
      });
    });
  }
  const camera = cameraAt(composition, frame, props), cameraPrev = frame > 0 ? cameraAt(composition, frame - 1, props) : camera;
  return { frame, width: composition.width, height: composition.height, background: composition.background ?? '#0c0e10', clips, camera, cameraPrev };
}

const MEDIA = new Set(['image', 'capture', 'video', 'ui']);
const num = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;

/** The affine from a layer's own box pixels to composition pixels at a composition frame, through its groups.
 * Box size follows render2: media layers default to the composition size, others need width and height. */
export function layerToComposition(composition: Composition, layerId: string, frame: number, props: Props = compositionProps(composition)): { matrix: Affine; size: [number, number] } {
  const W = composition.width, H = composition.height;
  const box = (layer: SceneLayer, local: number) => {
    const v = (name: string) => sampleField(layer, name, local, props, composition.fps);
    const media = MEDIA.has(layer.type), s = num(v('scale'), 1);
    const w = num(v('width'), media ? W : 0), h = num(v('height'), media ? H : 0);
    return { w, h, m: layerAffine({ x: num(v('x'), 0), y: num(v('y'), 0), w, h, ax: num(v('anchor_x'), 0), ay: num(v('anchor_y'), 0), rotate: num(v('rotate'), 0), sx: s * num(v('scale_x'), 1), sy: s * num(v('scale_y'), 1) }) };
  };
  const find = (layers: readonly SceneLayer[], local: number, parent: Affine): { matrix: Affine; size: [number, number] } | null => {
    for (const layer of layers) {
      const b = box(layer, local), m = compose(parent, b.m);
      if (layer.id === layerId) return { matrix: m, size: [b.w, b.h] };
      if (layer.type === 'group') { const hit = find(layer.layers ?? [], local, m); if (hit) return hit; }
    }
    return null;
  };
  for (const track of composition.tracks) for (const clip of track.clips) {
    // The layer is sampled inside its own clip (clamped), so a keyframe outside the clip still frames it.
    const local = Math.min(clip.duration - 1, Math.max(0, frame - clip.from));
    const hit = find(clip.layers, local, IDENTITY);
    if (hit) return hit;
  }
  throw new RangeError(`camera region names unknown layer "${layerId}"`);
}

const HOME = (c: Composition): CameraView => ({ x: c.width / 2, y: c.height / 2, zoom: 1, rotate: 0 });

/** One keyframe's framing: its region (in composition or layer px), then explicit overrides. */
export function cameraKeyView(composition: Composition, key: CameraKey, props: Props = compositionProps(composition)): CameraView {
  let view = HOME(composition);
  if (key.region) {
    const region = key.layer ? mapRegion(layerToComposition(composition, key.layer, key.frame, props).matrix, key.region) : key.region;
    view = regionView(region, composition.width, composition.height, key.padding ?? 0.08);
  }
  return { x: key.x ?? view.x, y: key.y ?? view.y, zoom: key.zoom ?? view.zoom, rotate: key.rotate ?? view.rotate };
}

/** The camera at a composition frame: holds before the first and after the last keyframe, and between two it
 * blends their framings (log-space zoom about the shared fixed point) with the later keyframe's easing. */
export function cameraAt(composition: Composition, frame: number, props: Props = compositionProps(composition)): CameraView {
  const keys = cameraKeys(composition, props);
  if (!keys.length) return HOME(composition);
  if (frame <= keys[0].frame) return cameraKeyView(composition, keys[0], props);
  const last = keys[keys.length - 1];
  if (frame >= last.frame) return cameraKeyView(composition, last, props);
  let i = 1;
  while (keys[i].frame <= frame) i++;
  const a = keys[i - 1], b = keys[i], length = b.frame - a.frame;
  const e = segmentEasing(b.easing ?? 'premium', composition.fps, length)((frame - a.frame) / length);
  return blendViews(cameraKeyView(composition, a, props), cameraKeyView(composition, b, props), e);
}

const FOLLOW = { leadS: 0.7, arriveS: 0.1, releaseS: 0.8 };   // push starts 0.7 s before the action, lands 0.1 s before
/** Every camera keyframe: the explicit ones, plus follow keys outside the explicit range (explicit wins inside it).
 * Follow keys depend on footage timing, which props can bind, so the list is cached per composition and props. */
export const cameraKeys = pairCache((composition: Composition, props: Props): CameraKey[] => {
  const explicit = composition.camera?.keyframes ?? [];
  if (!composition.camera?.follow) return explicit;
  const generated = followKeys(composition, props);
  if (!explicit.length) return generated;
  const first = explicit[0].frame, last = explicit[explicit.length - 1].frame;
  return [...generated.filter(k => k.frame < first || k.frame > last), ...explicit].sort((a, b) => a.frame - b.frame);
});

/** The props a composition is drawn with: its defaults under the caller's overrides, one object per pair, so the
 * picture, footage clocks, the camera and the sound plan all read the same values (opus b9ef3317). */
export const compositionProps: (composition: Composition, overrides?: Props) => Props = (() => {
  const merge = pairCache((composition: Composition, overrides: Props): Props => Object.freeze({ ...(composition.props ?? {}), ...overrides }));
  return (composition: Composition, overrides: Props = NO_PROPS) => merge(composition, overrides);
})();

/** Follow-camera keyframes from a cursor layer: push into each burst of activity in its recording, hold, release;
 * bursts close together pan from one framing to the next instead of zooming out between them. */
export function followKeys(composition: Composition, props: Props = compositionProps(composition)): CameraKey[] {
  const follow = composition.camera?.follow;
  if (!follow) return [];
  const fps = composition.fps, toCompFrames = (seconds: number) => seconds * fps.num / fps.den;
  for (const track of composition.tracks) for (const clip of track.clips) {
    const all: SceneLayer[] = [];
    const walk = (layers: readonly SceneLayer[]) => layers.forEach(l => { all.push(l); if (l.layers) walk(l.layers); });
    walk(clip.layers);
    const cursor = all.find(l => l.id === follow.cursor && l.type === 'cursor');
    if (!cursor) continue;
    const source = all.find(l => l.id === cursor.source && l.type === 'video');
    if (!source) throw new RangeError(`cursor "${cursor.id}" needs its source video in the same clip`);
    const recFps = (cursor.fps as RationalFps | undefined) ?? fps;
    const boxW = num(source.width, composition.width), boxH = num(source.height, composition.height);
    const viewport = (cursor.viewport as [number, number] | undefined) ?? [boxW, boxH];
    const k = boxW / viewport[0];
    const offset = clip.offset ?? 0;
    // Recording seconds -> composition frame, through the video's own clock (trim, rate, speed ramps) and the offset.
    const clock = timeMap(source, clip.duration + offset, props, fps);
    const frameOf = (t: number) => clip.from + clock.frameAt(t) - offset;
    // Only bursts the clip actually shows: one inside the trimmed-away head or past the clip's end would clamp onto
    // its first or last frame and frame something that never happens on screen.
    const shown = [clock.seconds(offset), clock.seconds(offset + clip.duration)];
    const shots = followShots(cursor.events as CursorEvent[], recFps, viewport, { zoom: follow.zoom, idleMs: follow.idle_ms })
      .filter(shot => shot.end > shown[0] && shot.start < shown[1]);
    const lo = clip.from, hi = clip.from + clip.duration - 1, clampF = (f: number) => Math.round(Math.min(hi, Math.max(lo, f)));
    const home = (frame: number): CameraKey => ({ frame: clampF(frame), x: composition.width / 2, y: composition.height / 2, zoom: 1, rotate: 0, easing: 'premium' });
    const keys: CameraKey[] = [];
    shots.forEach((shot, i) => {
      const region: [number, number, number, number] = [shot.region[0] * k, shot.region[1] * k, shot.region[2] * k, shot.region[3] * k];
      const arrive = frameOf(shot.start) - toCompFrames(FOLLOW.arriveS), push = frameOf(shot.start) - toCompFrames(FOLLOW.leadS);
      const next = shots[i + 1], nextPush = next ? frameOf(next.start) - toCompFrames(FOLLOW.leadS) : Infinity;
      // Release unless the next burst starts before this shot could zoom out and back in. When it pans on instead,
      // the hold ends where the next push starts, so the pan always gets leadS - arriveS rather than a jump cut.
      const release = nextPush > frameOf(shot.end) + toCompFrames(FOLLOW.releaseS);
      const hold = release ? frameOf(shot.end) : Math.min(frameOf(shot.end), nextPush);
      const joined = keys.length > 0 && keys[keys.length - 1].region !== undefined;   // the previous shot is still held
      if (!joined) keys.push(home(push));
      keys.push({ frame: clampF(arrive), layer: source.id, region, padding: 0.04, easing: 'premium' });
      keys.push({ frame: clampF(Math.max(arrive, hold)), layer: source.id, region, padding: 0.04, easing: 'premium' });
      if (release) keys.push(home(hold + toCompFrames(FOLLOW.releaseS)));
    });
    // Frames must not go backwards after clamping; a later key at the same frame acts as a cut.
    for (let i = 1; i < keys.length; i++) if (keys[i].frame < keys[i - 1].frame) keys[i] = { ...keys[i], frame: keys[i - 1].frame };
    return keys;
  }
  throw new RangeError(`camera follows unknown cursor layer "${follow.cursor}"`);
}
