/* Recorded pointer (28cb5faf, desktop d590dce6 sidecar, backend cursor layer). Pure functions: a smoothed cursor
 * path and click ripples from the recording's events, and the follow camera's keyframes from the same events.
 * Times are recording seconds; the caller maps them through the video's trim and rate. */
import { integer } from './math';
import { springStep } from './spring';
import type { RationalFps } from './frame';

export interface CursorEvent { frame: number; type: 'move' | 'down' | 'up' | 'click' | 'scroll' | 'key'; x?: number; y?: number; button?: string; rect?: number[] }
export interface CursorSample { x: number; y: number; visible: boolean; clickAge: number | null }

const STEP_S = 1 / 240;            // fixed simulation step: the path never depends on the output frame rate
const CURSOR_OMEGA = 22;           // critically damped follow: settles in about 0.25 s, no overshoot
const RIPPLE_S = 0.5;
const ARRIVE_S = 0.3;              // the spring's settle time: a press is reached by then, never after it
const POINTER_TYPES = new Set(['move', 'down', 'up', 'click', 'scroll']);   // a key carries no pointer position

const seconds = (frame: number, fps: RationalFps) => integer(frame, 'event frame', 0) * fps.den / fps.num;
const isPress = (e: CursorEvent) => e.type === 'click' || e.type === 'down';

interface Target { t: number; x: number; y: number }

/** When a ripple starts: on each down, and on a click only when no down came within RIPPLE_S before it, so a
 * physical click (down, then click about 100 ms later) draws one ripple rather than restarting it (opus N2). */
function rippleTimes(events: readonly CursorEvent[], fps: RationalFps): number[] {
  let lastDown = -Infinity;
  const times: number[] = [];
  for (const e of events) {
    const t = seconds(e.frame, fps);
    if (e.type === 'down') { lastDown = t; times.push(t); }
    else if (e.type === 'click' && t - lastDown >= RIPPLE_S) times.push(t);
  }
  return times;
}

/** Where the spring heads, and from when. Scripted input often jumps the pointer one frame before a press; the
 * travel to a pressed point then starts ARRIVE_S before the press (never before the previous target), so the
 * drawn pointer lands on the press instead of arriving after the click it shows. */
function pointerTargets(events: readonly CursorEvent[], fps: RationalFps): Target[] {
  const placed = events.filter(e => POINTER_TYPES.has(e.type) && typeof e.x === 'number' && typeof e.y === 'number');
  const targets: Target[] = [];
  placed.forEach((e, i) => {
    const t = seconds(e.frame, fps), prev = targets[targets.length - 1];
    if (prev && prev.x === e.x && prev.y === e.y) return;            // same point: the spring is already heading there
    const press = placed.slice(i).find(p => p.x !== e.x || p.y !== e.y || isPress(p));
    const pressAt = press && isPress(press) && press.x === e.x && press.y === e.y ? seconds(press.frame, fps) : null;
    const lead = pressAt !== null && pressAt - t < ARRIVE_S ? pressAt - ARRIVE_S : t;
    targets.push({ t: prev ? Math.max(prev.t, lead) : t, x: e.x!, y: e.y! });
  });
  return targets;
}

/** A smoothed pointer: a critically damped spring chases each target, simulated once at a fixed step, so every
 * frame reads the same point however it is seeked. The pointer appears at the first positioned pointer event. */
export function cursorTrack(events: readonly CursorEvent[], fps: RationalFps): (t: number) => CursorSample {
  const targets = pointerTargets(events, fps);
  const clicks = rippleTimes(events, fps);
  if (!targets.length) return () => ({ x: 0, y: 0, visible: false, clickAge: null });
  const start = targets[0].t, end = targets[targets.length - 1].t + 1;
  const n = Math.ceil((end - start) / STEP_S) + 1;
  const xs = new Float64Array(n), ys = new Float64Array(n);
  let x = targets[0].x, y = targets[0].y, vx = 0, vy = 0, k = 0;
  for (let i = 0; i < n; i++) {
    const t = start + i * STEP_S;
    while (k + 1 < targets.length && targets[k + 1].t <= t) k++;
    if (i) { [x, vx] = springStep(x, vx, targets[k].x, STEP_S, CURSOR_OMEGA); [y, vy] = springStep(y, vy, targets[k].y, STEP_S, CURSOR_OMEGA); }
    xs[i] = x; ys[i] = y;
  }
  return t => {
    if (t < start) return { x: xs[0], y: ys[0], visible: false, clickAge: null };
    const f = Math.min(n - 1, (t - start) / STEP_S), i = Math.floor(f), j = Math.min(n - 1, i + 1), u = f - i;
    let last: number | null = null;
    for (const c of clicks) if (c <= t) last = c; else break;
    const age = last === null ? null : t - last;
    return { x: xs[i] + (xs[j] - xs[i]) * u, y: ys[i] + (ys[j] - ys[i]) * u, visible: true, clickAge: age !== null && age < RIPPLE_S ? age : null };
  }
}

export interface FollowOptions { zoom?: number; idleMs?: number }
export interface FollowShot { start: number; end: number; region: [number, number, number, number] }

/** Where the follow camera should be: one shot per burst of activity (clicks, typing, scrolling; gaps under
 * idleMs join a burst). Each shot frames the first click's target rect, or a viewport/zoom box centred on it,
 * from a little before the burst until idleMs after it. Regions are in viewport px; times in recording seconds. */
export function followShots(events: readonly CursorEvent[], fps: RationalFps, viewport: readonly [number, number], options: FollowOptions = {}): FollowShot[] {
  const zoom = options.zoom ?? 2.2, idle = (options.idleMs ?? 1200) / 1000;
  const active = events.filter(e => e.type !== 'move' && e.type !== 'up');
  const shots: FollowShot[] = [];
  let burst: CursorEvent[] = [];
  const close = () => {
    if (!burst.length) return;
    const anchor = burst.find(e => e.type === 'click' || e.type === 'down') ?? burst.find(e => typeof e.x === 'number');
    const t0 = seconds(burst[0].frame, fps), t1 = seconds(burst[burst.length - 1].frame, fps);
    if (anchor) {
      const [vw, vh] = viewport, w = vw / zoom, h = vh / zoom;
      const rect = anchor.rect && anchor.rect[2] > 0 && anchor.rect[3] > 0 ? anchor.rect : null;
      // A small target still gets the follow zoom's framing around it, never a 10x push onto a button.
      const cx = rect ? rect[0] + rect[2] / 2 : anchor.x!, cy = rect ? rect[1] + rect[3] / 2 : anchor.y!;
      const rw = Math.max(w, rect ? rect[2] : 0), rh = Math.max(h, rect ? rect[3] : 0);
      const x = Math.min(Math.max(0, cx - rw / 2), Math.max(0, vw - rw)), y = Math.min(Math.max(0, cy - rh / 2), Math.max(0, vh - rh));
      shots.push({ start: t0, end: t1 + idle, region: [x, y, Math.min(rw, vw), Math.min(rh, vh)] });
    }
    burst = [];
  };
  for (const e of active) {
    if (burst.length && seconds(e.frame, fps) - seconds(burst[burst.length - 1].frame, fps) > idle) close();
    burst.push(e);
  }
  close();
  return shots;
}
