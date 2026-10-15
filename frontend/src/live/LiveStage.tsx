/* The Live stage (task b42437b9): the team as boxless people. Each motion means one thing:
 * gathering = working together (a shared task, a review, a follow-up or a recent exchange), comet = @mention,
 * baton = handoff, gold rise = a question for you, ring step = a tool ran, one head shake = a failed turn.
 * At rest (task 4cbce498, Zero #18047) people breathe: a small bob for everyone (Zero #18710), an orbit for whoever is
 * working while their work moves, a gold ripple for whoever waits on you. That ambient motion is CSS (see live.css
 * for its cost), so the script loop below still stops when nothing travels (Zero 0bb2bdcf).
 * People are buttons (keyboard reachable); effects are drawn on an aria-hidden canvas and always have a
 * plain-text twin, and the Latest rail lists what changed as buttons. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { isVideo, type LiveAgent, type LiveFeed, type LiveInteraction } from './liveFeed';
import { plainSpeech, SpeechBubble } from '../components/SpeechBubble';
import { bubbleClear, collabGroups, LABEL_H, LINK_HOLD_S, layoutStage, nextLinkExpiry, overlap, placeBubble, speechBlocker, springStep,
  type BubbleSide, type CollabGroup, type CollabLink, type Point, type Rect } from './liveLayout';

// Caps sized for a 13-person room (Zero #18047: three of each showed about a third of what happened).
const MAX_MOVERS = 6;          // at most six people travel at once; the rest wait their turn
const MAX_EFFECTS = 6;         // at most six comets or batons in the air
const EFFECT_STALE_MS = 4000;  // a comet still waiting for a slot this long is dropped, never replayed late
// The animation loop runs only while something moves (Zero 0bb2bdcf: an always-on loop kept a CPU core busy).
const MAX_FRAME_MS = 50;       // a frame never advances motion further than this, however late it runs
const SETTLE = { px: 0.25, pxPerS: 1 };       // a spring this close and this slow is at rest: snap it and stop
const SHAKE = { ms: 480, cycles: 34, px: 7 }; // one head shake after a failed turn
const HOP = { ms: 320, px: 8 };               // the small hop when a comet or baton lands
const EFFECT_TAIL_MS = 1700;   // an effect stays drawn this long after it lands (ring, fade)
// A bubble stays while its person is active and for a while after their newest words, whatever state they are
// in now (a finished turn, a reviewer's posted verdict), so the last thing said stays readable.
const SPEECH_SHOWN_S = 14, SPEECH_AFTER_S = 9;
const STIR_S = 6;   // a working person's orbit runs this long after their latest real event (live.css lv-stir)
/** How many bubbles a stage this size can show without crowding: one per ~140k px², from 2 up to 7. */
const bubbleCap = (w: number, h: number) => Math.max(2, Math.min(7, Math.floor(w * h / 140000)));
// Speech bubbles are sized to their text (Zero #14496: bubbles were cut off), measured before placement so
// overlap avoidance uses the real size, then resized with a soft ease as the text grows or a shorter
// message replaces a longer one.
const BUBBLE_FONT = '12.5px Inter, "Segoe UI", system-ui, sans-serif', BUBBLE_NAME_FONT = '600 ' + BUBBLE_FONT;
const BUBBLE_LINE = 17, BUBBLE_PAD_X = 23, BUBBLE_PAD_Y = 12, BUBBLE_MIN = 120, BUBBLE_MAX = 280, BUBBLE_LINES = 5;
// A crowded spot gets a narrower bubble (its page wraps to more lines), then one a little farther out.
const BUBBLE_NARROW = 190, BUBBLE_REACH = 230, BUBBLE_NARROW_LINES = 8;
let measureCtx: CanvasRenderingContext2D | null = null;
/** Lays out a bubble's words as the bubble wraps them (name first, in bold), one word at a time. */
function wordWrap(name: string, max: number) {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d');
  const ctx = measureCtx!;
  const inner = max - BUBBLE_PAD_X;
  ctx.font = BUBBLE_NAME_FONT; let x = ctx.measureText(name + ' ').width;
  ctx.font = BUBBLE_FONT;
  const space = ctx.measureText(' ').width;
  let lines = 1, widest = x;
  const newLine = () => { lines++; widest = Math.max(widest, x - space); x = 0; };
  /** Places a word's characters, stopping before any that would start line `maxLines + 1`; returns how many fit. */
  const place = (word: string, maxLines: number) => {
    const w = ctx.measureText(word).width;
    if (x > 0 && x + w > inner) { if (lines >= maxLines) return 0; newLine(); }
    if (w <= inner) { x += w + space; return word.length; }
    // Longer than a line (a link, a hash): .sb-text's overflow-wrap: anywhere breaks it wherever it overflows.
    let n = 0;
    for (const ch of word) {
      const c = ctx.measureText(ch).width;
      if (x > 0 && x + c > inner) { if (lines >= maxLines) return n; newLine(); widest = inner; }
      x += c; n += ch.length;
    }
    x += space;
    return n;
  };
  return {
    add(word: string) { place(word, Infinity); return lines; },
    /** The longest start of `word` that keeps the layout within `maxLines`, in characters. */
    fit(word: string, maxLines: number) { return place(word, maxLines); },
    get lines() { return lines; },
    get widest() { return Math.max(widest, x - space); },
  };
}
export function bubbleSize(name: string, text: string, max = BUBBLE_MAX, maxLines = BUBBLE_LINES): { w: number; h: number; lines: number } {
  const wrap = wordWrap(name, max);
  for (const word of plainSpeech(text).split(/\s+/).filter(Boolean)) wrap.add(word);
  return { w: Math.ceil(Math.min(max, Math.max(BUBBLE_MIN, wrap.widest + BUBBLE_PAD_X + 2))), h: Math.min(wrap.lines, maxLines) * BUBBLE_LINE + BUBBLE_PAD_Y, lines: wrap.lines };
}

// Reading pace (Zero #18710: bubbles cut text off because they scrolled as fast as it streamed). A closed bubble
// shows one page: whole sentences that fit a full-width bubble, so nothing is cut or scrolled away. Each page
// stays up for its reading time. While a reply streams, its page fills word by word and the next page waits its
// turn. Someone who talks faster than people read skips ahead to their newest sentences, and opening a bubble
// shows the whole reply.
const READ_WPM = 230, PAGE_MIN_S = 2.4, PAGE_EXTRA_S = 1;
const PAGE_BACKLOG_CHARS = 700;   // more unread than this, and the next page starts at the newest sentences
const STREAM_S = 2;               // words this fresh may still be mid-word: the last one waits for the next
const PAGE_WAIT_S = 20;           // a page never yet on show waits this long for room beside its person, then lapses
const wordsReadS = (words: string) => words.split(/\s+/).filter(Boolean).length / READ_WPM * 60;
const SENTENCE_END = /[.!?…:]["')\]]*$/;
/** One person's place in what they are saying: the text read so far, the page on show, when that page opened
 * (`at`), when a reader who started then finishes the words on it so far (`readBy`), and whether it has been on
 * show yet (`seen`): a page still waiting for room has not been read. */
interface Reading { text: string; start: number; end: number; at: number; readBy: number; streaming: boolean; seen: boolean }
/** The page grew from `r.end` to `end` at `now`: the reader reads the new words after the ones before, and not
 * before they arrived, so words that land late on a page get their own reading time (design 9980f446). */
const grown = (r: Reading, text: string, end: number, now: number) =>
  end > r.end ? Math.max(r.readBy, now) + wordsReadS(text.slice(r.end, end)) : r.readBy;
/** When a page may turn: once its words are read, plus a beat, and never sooner than PAGE_MIN_S after it opened. */
const pageTurnAt = (r: Reading) => Math.max(r.at + PAGE_MIN_S, r.readBy + PAGE_EXTRA_S);
/** Where the page that starts at `start` ends: as many words as fit, never fewer than the `shown` end (what is
 * already on screen stays), and, when the page is full, after its last whole sentence if one ends past `shown`. */
function pageEnd(name: string, text: string, start: number, shown: number, streaming: boolean): number {
  const wrap = wordWrap(name, BUBBLE_MAX), word = /\S+/g;
  word.lastIndex = start;
  let end = Math.max(start, shown), sentence = -1, full = false;
  for (let m = word.exec(text); m; m = word.exec(text)) {
    const e = m.index + m[0].length;
    if (streaming && e === text.length) break;   // possibly half a word
    // A page already on its last lines ends with its sentence rather than start one it can't finish.
    if (e > end && sentence === end && end > start && wrap.lines >= BUBBLE_LINES - 1) { full = true; break; }
    if (wrap.add(m[0]) > BUBBLE_LINES) {
      // A page's first word longer than the whole page (a long link) is cut where the page fills; the rest of it
      // starts the next page, so reading never stalls and nothing is clipped (design 9980f446).
      if (end === start) end = m.index + Math.max(1, wordWrap(name, BUBBLE_MAX).fit(m[0], BUBBLE_LINES));
      full = true; break;
    }
    if (e > end) end = e;
    if (SENTENCE_END.test(m[0])) sentence = e;
  }
  return full && sentence > shown ? sentence : end;
}
/** Where reading resumes after a skip: the first sentence start in the newest PAGE_BACKLOG_CHARS. */
function catchUp(text: string): number {
  const from = text.length - PAGE_BACKLOG_CHARS, tail = text.slice(from);
  const s = tail.search(/[.!?…]\s+\S/);
  if (s >= 0) return from + s + tail.slice(s).search(/\s\S/) + 1;
  const space = tail.indexOf(' ');
  return space >= 0 ? from + space + 1 : from;
}
const ACTIVE: LiveAgent['state'][] = ['working', 'waiting_on_user'];

export interface StageProps {
  feed: LiveFeed;
  reduced: boolean;
  paused: boolean;
  /** Reserved top-left corner where questions for you rise to (the You area). */
  youArea: Rect;
  selectedPerson: string | null;
  selectedTask: string | null;
  onSelectPerson: (id: string) => void;
  onFollowWork: (taskId: string) => void;
}

interface Body { x: number; y: number; vx: number; vy: number; tx: number; ty: number; waitingSince: number; shakeAt: number; hopAt: number; ring: number }
interface Effect { kind: 'comet' | 'baton' | 'gold' | 'answer' | 'broadcast'; from: string; to: string; label: string; color: string; start: number; dur: number; landed?: boolean; queuedAt?: number }

/** The bob's shared step grid (live.css lv-bob): every bob changes on the same 1/15 s frames, so 13 people cost
 * one 15 fps redraw rather than thirteen interleaved ones (Zero #18710). */
const BOB_STEP_MS = 1000 / 15, BOB_STEPS = 60;   // a 4 s loop is 60 steps
/** Each person's own place on the bob loop, stable for their id and a whole number of grid steps, so the room
 * never bobs in step and still changes on the shared frames. */
function rhythm(id: string): CSSProperties {
  let h = 2166136261; for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  const u = (h >>> 0) / 4294967296, step = (h >>> 12) % BOB_STEPS;
  return { '--bob-at': `${(-step * BOB_STEP_MS / 1000).toFixed(6)}s`, '--orbit-at': `${(-u * 3).toFixed(2)}s` } as CSSProperties;
}
/** Moves each running bob's start onto the shared grid. A CSS animation starts on whatever frame its style
 * applied, so people who appear later (or after a view switch) would otherwise step on frames of their own. */
function alignBobs(root: HTMLElement) {
  for (const anim of root.getAnimations({ subtree: true })) {
    if ((anim as CSSAnimation).animationName !== 'lv-bob') continue;
    void anim.ready.then(() => {
      // Only a running bob: setting a paused animation's start time would resume it against the CSS pause.
      if (anim.playState !== 'running' || anim.startTime === null) return;
      const start = Number(anim.startTime), snapped = Math.round(start / BOB_STEP_MS) * BOB_STEP_MS;
      if (Math.abs(start - snapped) > 0.5) anim.startTime = snapped;
    }, () => undefined);
  }
}
// The Latest rail: what changed, newest first, each line opening its work.
const RAIL = { w: 330, row: 24, head: 26, pad: 10 };

const initials = (name: string) => { const w = name.split(/\s+/).filter(Boolean); return (w.length === 1 && w[0].length <= 3 ? w[0] : w.slice(0, 2).map(x => x[0]).join('')).toUpperCase() || '?'; };

export function LiveStage({ feed, reduced, paused, youArea, selectedPerson, selectedTask, onSelectPerson, onFollowWork }: StageProps) {
  const root = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 900, h: 560 });
  const bodies = useRef(new Map<string, Body>());
  const nodes = useRef(new Map<string, HTMLButtonElement>());
  const effects = useRef<Effect[]>([]), queued = useRef<Effect[]>([]);
  const seen = useRef(new Set<string>());
  const prevState = useRef(new Map<string, string>()), prevActivity = useRef(new Map<string, string>());
  const joined = useRef(new Map<string, { group: string; at: number }>());
  // Each bubble's real box as the shared component reports it (the size it is easing to), and the spot an
  // open bubble keeps while someone reads it, so growing never moves it out from under the pointer.
  const bubbleBox = useRef(new Map<string, { w: number; h: number; open: boolean }>());
  const heldSpot = useRef(new Map<string, { rect: Rect; side: BubbleSide; dir?: 'down' | 'up' }>());
  /** Bubbles drawn last render: the page each showed, since when it has been on show, on which side and, for one
   * placed farther out, its centre's offset from the person. */
  const onScreen = useRef(new Map<string, { page: string; since: number; side?: BubbleSide; ring?: Point }>());
  /** When each person's bubble was last on show (wall seconds), so neighbours take turns. */
  const lastShown = useRef(new Map<string, number>());
  const [, setBubbleTick] = useState(0);
  const openRoom = useRef(new Map<string, number>());   // the clear height an open bubble may grow into
  const live = useRef({ paused, reduced }); live.current = { paused, reduced };
  const clock = useRef(0);   // animation time in ms; frozen while paused
  const kick = useRef(() => {});   // starts the animation loop if it is resting; set by the loop's effect

  useLayoutEffect(() => {
    const el = root.current; if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // The stage's wall clock (s). Pause shows LiveTeam's snapshot of the room, so it also stops this clock: no
  // bubble or relation expires on a paused stage (design 14b0b5b1). Resume reads real time against the current
  // records again, and whatever ran out meanwhile goes then.
  const pausedAt = useRef<number | null>(null);
  if (paused) pausedAt.current ??= Date.now() / 1000; else pausedAt.current = null;
  const wallNow = () => pausedAt.current ?? Date.now() / 1000;

  // Who is with whom: collabGroups over the people's current work and the room's live relations. Relations
  // expire with time, so a timer (never a frame loop) re-reads them when the next one ends.
  const [linkTick, setLinkTick] = useState(0);
  const relations = useMemo(() => collabRelations(feed, wallNow()), [feed.links, feed.interactions, feed.agents, linkTick, paused]); // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useMemo(() => collabGroups(feed.agents.map(a => ({ id: a.id, active: ACTIVE.includes(a.state), task_id: a.task_id, task_title: a.task_title })), relations, wallNow()),
    [feed.agents, relations]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (paused) return;
    const wait = nextLinkExpiry(relations, Date.now() / 1000) * 1000 - Date.now();
    if (!Number.isFinite(wait)) return;
    const t = window.setTimeout(() => setLinkTick(n => n + 1), Math.max(250, wait + 50));
    return () => window.clearTimeout(t);
  }, [relations, paused]);
  // Group membership: the time each person joined fixes their seat order inside the group.
  const people = useMemo(() => feed.agents.map(a => {
    const g = groups.get(a.id), group = g?.key ?? '';
    const j = joined.current.get(a.id);
    if (!j || j.group !== group) joined.current.set(a.id, { group, at: Date.now() });
    // Someone pulled into a group by a relation keeps full size there, even while idle.
    const quiet = !g && (a.state === 'idle' || a.state === 'stopped');
    return { id: a.id, order: a.order, group, label: g ? `◆ ${chipText(g, feed)} · talking · 00` : undefined, joinedAt: joined.current.get(a.id)!.at, quiet, hasThumb: !!a.artifact };
  }), [feed.agents, groups]); // eslint-disable-line react-hooks/exhaustive-deps
  const stage: Rect = { x: 0, y: 0, w: size.w, h: size.h };
  // The Latest rail sits bottom-left and is reserved like the You area: nobody is seated or speaks under it.
  const railRows = size.h >= 640 ? 5 : size.h >= 460 ? 3 : 0;
  const railH = RAIL.head + RAIL.row * railRows + RAIL.pad;
  const rail: Rect | null = railRows && size.w >= 560 ? { x: 12, y: size.h - 12 - railH, w: Math.min(RAIL.w, size.w - 24), h: railH } : null;
  const spots = useRef(new Map<string, Rect>());
  const layout = useMemo(() => {
    const next = layoutStage(people, stage, rail ? [youArea, rail] : [youArea], spots.current, rail ? [rail] : []);
    spots.current = new Map(next.formations.map(f => [f.group, f.box]));
    return next;
  }, [people, size.w, size.h, youArea.x, youArea.y, youArea.w, youArea.h, rail?.y, rail?.w]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seats and per-person events (state change shake, tool ring step).
  useEffect(() => {
    const t = clock.current;
    for (const a of feed.agents) {
      const seat = layout.seats.get(a.id); if (!seat) continue;
      let b = bodies.current.get(a.id);
      if (!b) { b = { x: seat.x, y: seat.y, vx: 0, vy: 0, tx: seat.x, ty: seat.y, waitingSince: 0, shakeAt: -1e9, hopAt: -1e9, ring: 0 }; bodies.current.set(a.id, b); }
      if (b.tx !== seat.x || b.ty !== seat.y) {
        b.tx = seat.x; b.ty = seat.y; b.waitingSince = t;
        // Paused, the feed is a snapshot, so only the stage's shape moved this seat (the watch session opened, a
        // resize). Pause stops time, not geometry: step straight there so chips, floors and bubbles still match.
        if (paused) { b.x = seat.x; b.y = seat.y; b.vx = b.vy = 0; }
      }
      if (a.state === 'failed' && prevState.current.get(a.id) && prevState.current.get(a.id) !== 'failed') b.shakeAt = t;
      if (a.activity && prevActivity.current.has(a.id) && prevActivity.current.get(a.id) !== a.activity) b.ring += 60;
      prevState.current.set(a.id, a.state); prevActivity.current.set(a.id, a.activity);
    }
    for (const id of [...bodies.current.keys()]) if (!layout.seats.has(id)) bodies.current.delete(id);
    kick.current();
  }, [feed.agents, layout]);

  // New interactions become effects (history from the snapshot never animates).
  useEffect(() => {
    const color = (id: string) => feed.agents.find(a => a.id === id)?.color || '#54d9ed';
    const name = (id: string) => id === 'user' ? 'you' : id === 'team' ? 'everyone' : (feed.agents.find(a => a.id === id)?.short || id);
    for (const i of [...feed.interactions].reverse()) {
      if (seen.current.has(i.id)) continue;
      seen.current.add(i.id);
      if (!i.fresh) continue;
      const e = effectFor(i, color, name);
      if (e) queued.current.push({ ...e, queuedAt: performance.now() });
    }
    if (queued.current.length) kick.current();
  }, [feed.interactions, feed.agents]);

  // Pausing freezes the clock and lets the loop rest; resuming or switching reduced motion restarts it.
  useEffect(() => { kick.current(); }, [paused, reduced]);
  // Bobs restart when someone joins, on resume and when motion comes back: put them on the shared grid again.
  const memberKey = feed.agents.map(a => a.id).join(' ');
  useEffect(() => { if (root.current && !paused && !reduced) alignBobs(root.current); }, [memberKey, paused, reduced]);

  // Animation loop: springs toward seats (three movers at most), shake, hop and effects on canvas. It runs
  // only while one of those is in motion and rests otherwise; kick() restarts it on the event that creates
  // work (a seat or state change, a new interaction, resume). Nothing moves at rest: each motion means one thing.
  useEffect(() => {
    let raf = 0, last = 0, canvasDrawn = false;
    const ctx = canvas.current?.getContext('2d');
    const written = new WeakMap<HTMLElement, { transform: string; ring: string }>();   // skip unchanged writes
    const schedule = () => { if (!raf) raf = requestAnimationFrame(frame); };
    const frame = (now: number) => {
      raf = 0;
      const real = last ? Math.min(MAX_FRAME_MS, now - last) : 0; last = now;
      const { paused, reduced } = live.current;
      // One animation clock for motion, effects, shake and hop. It stands still while paused, so effects
      // freeze exactly and nothing snaps on resume, at any refresh rate.
      if (!paused) clock.current += real;
      const t = clock.current, dt = paused ? 0 : real / 1000;
      let busy = false;
      if (!paused) {
        const moving = [...bodies.current.values()].filter(b => Math.hypot(b.tx - b.x, b.ty - b.y) > 2);
        const allowed = new Set(reduced ? moving : moving.sort((p, q) => p.waitingSince - q.waitingSince).slice(0, MAX_MOVERS));
        for (const b of bodies.current.values()) {
          if (reduced) { b.x = b.tx; b.y = b.ty; b.vx = b.vy = 0; continue; }
          if (!allowed.has(b) && Math.hypot(b.tx - b.x, b.ty - b.y) > 2) { busy = true; continue; }   // waiting its turn
          [b.x, b.vx] = springStep(b.x, b.vx, b.tx, dt); [b.y, b.vy] = springStep(b.y, b.vy, b.ty, dt);
          if (Math.abs(b.tx - b.x) < SETTLE.px && Math.abs(b.ty - b.y) < SETTLE.px && Math.hypot(b.vx, b.vy) < SETTLE.pxPerS) {
            b.x = b.tx; b.y = b.ty; b.vx = b.vy = 0;
          } else busy = true;
        }
        queued.current = queued.current.filter(e => now - (e.queuedAt ?? now) < EFFECT_STALE_MS);
        while (effects.current.length < MAX_EFFECTS && queued.current.length) effects.current.push({ ...queued.current.shift()!, start: t });
      }
      for (const [id, el] of nodes.current) {
        const b = bodies.current.get(id); if (!b) continue;
        let x = b.x, y = b.y;
        // Offsets come from the frozen clock while paused, so pausing holds people exactly where they are.
        if (!reduced) {
          const s = (t - b.shakeAt) / SHAKE.ms; if (s >= 0 && s < 1) { x += Math.sin(s * SHAKE.cycles) * SHAKE.px * (1 - s); busy ||= !paused; }
          const h = (t - b.hopAt) / HOP.ms; if (h >= 0 && h < 1) { y -= HOP.px * Math.sin(Math.PI * h); busy ||= !paused; }
        }
        const transform = `translate(${x}px, ${y}px)`, ring = `${b.ring}deg`, prev = written.get(el);
        if (prev?.transform !== transform) el.style.transform = transform;
        if (prev?.ring !== ring) el.style.setProperty('--ring', ring);
        written.set(el, { transform, ring });
      }
      if (ctx && canvas.current) {
        if (effects.current.length) {
          drawEffects(ctx, canvas.current, t, paused, reduced); canvasDrawn = true;
          busy ||= !paused;
        } else if (canvasDrawn) {
          ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.current.width, canvas.current.height); canvasDrawn = false;
        }
      }
      if (busy || queued.current.length && !paused) schedule(); else last = 0;
    };
    kick.current = schedule;
    schedule();   // place everyone once on mount
    return () => { cancelAnimationFrame(raf); kick.current = () => {}; };
  }, []);

  function centre(id: string): Point {
    if (id === 'user') return { x: youArea.x + 28, y: youArea.y + youArea.h / 2 };
    const b = bodies.current.get(id); return b ? { x: b.x, y: b.y } : { x: size.w / 2, y: size.h / 2 };
  }
  function drawEffects(ctx: CanvasRenderingContext2D, c: HTMLCanvasElement, t: number, paused: boolean, reduced: boolean) {
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== c.clientWidth * dpr) { c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, c.clientWidth, c.clientHeight);
    for (const e of effects.current) {
      const k = reduced ? 1 : Math.min(1, (t - e.start) / e.dur);
      const A = centre(e.from), B = centre(e.to);
      const mx = (A.x + B.x) / 2 + (B.y - A.y) * 0.22, my = (A.y + B.y) / 2 - (B.x - A.x) * 0.22 - 24;
      const at = (u: number) => ({ x: (1 - u) ** 2 * A.x + 2 * (1 - u) * u * mx + u * u * B.x, y: (1 - u) ** 2 * A.y + 2 * (1 - u) * u * my + u * u * B.y });
      const ease = (u: number) => u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
      if (e.kind === 'broadcast') {
        // @all: rings ripple out from the sender, no comet to anyone in particular.
        const age = (t - e.start) / e.dur; if (age > 1) continue;
        for (let n = 0; n < 3; n++) { const k2 = age - n * 0.18; if (k2 < 0 || k2 > 1) continue;
          ctx.globalAlpha = (1 - k2) * 0.7; ctx.strokeStyle = e.color; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(A.x, A.y, 26 + (reduced ? 30 : 150 * k2), 0, 7); ctx.stroke(); }
        ctx.globalAlpha = 1; ctx.font = '600 11px Inter, Segoe UI, sans-serif'; ctx.textAlign = 'center'; ctx.lineWidth = 3; ctx.strokeStyle = '#0e1013';
        ctx.strokeText(e.label, A.x, A.y - 42); ctx.fillStyle = e.color; ctx.fillText(e.label, A.x, A.y - 42);
        continue;
      }
      if (reduced) {
        // Static direction cue: a faint line with an arrow head, then fade.
        const age = (t - e.start) / 1600; if (age > 1) continue;
        ctx.globalAlpha = 0.6 * (1 - age); ctx.strokeStyle = e.color; ctx.lineWidth = 1.5; ctx.setLineDash([4, 5]);
        ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.quadraticCurveTo(mx, my, B.x, B.y); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
        if (!e.landed) { e.landed = true; land(e, t); }
        continue;
      }
      if (k < 1) {
        const q = at(ease(k));
        if (e.kind === 'baton') {
          ctx.save(); ctx.translate(q.x, q.y); ctx.rotate(k * 9); ctx.fillStyle = e.color; ctx.fillRect(-6, -6, 12, 12); ctx.restore();
        } else {
          for (let i = 0; i < 12; i++) { const p = at(ease(Math.max(0, k - i * 0.022))); ctx.globalAlpha = (1 - i / 12) * 0.9; ctx.fillStyle = e.kind === 'gold' || e.kind === 'answer' ? '#f0c674' : e.color; ctx.beginPath(); ctx.arc(p.x, p.y, 4.5 - i * 0.3, 0, 7); ctx.fill(); }
          ctx.globalAlpha = 1;
        }
        const m = at(0.5); ctx.font = '600 11px Inter, Segoe UI, sans-serif'; ctx.textAlign = 'center';
        ctx.lineWidth = 3; ctx.strokeStyle = '#0e1013'; ctx.strokeText(e.label, m.x, m.y - 10); ctx.fillStyle = e.kind === 'gold' ? '#f0c674' : e.color; ctx.fillText(e.label, m.x, m.y - 10);
      } else {
        if (!e.landed) { e.landed = true; land(e, t); }
        const r = (t - e.start - e.dur) / 500;
        if (r < 1) { ctx.globalAlpha = 1 - r; ctx.strokeStyle = e.kind === 'gold' ? '#f0c674' : e.color; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(B.x, B.y, 24 + 20 * r, 0, 7); ctx.stroke(); ctx.globalAlpha = 1; }
      }
    }
    effects.current = effects.current.filter(e => (t - e.start) < e.dur + EFFECT_TAIL_MS);
  }
  function land(e: Effect, t: number) { const b = bodies.current.get(e.to); if (b) b.hopAt = t; }

  // Speech: the newest speakers the stage has room for, each in a clear spot or as a cue by the person.
  const now = wallNow();
  const blockers: Rect[] = [youArea, ...(rail ? [rail] : []), ...layout.formations.map(f => ({ ...f.box, h: 22 })),
    ...people.map(p => { const s = layout.seats.get(p.id); return s ? speechBlocker(s, p.quiet, p.hasThumb && layout.thumbs) : null; }).filter((r): r is Rect => !!r)];
  const isOpen = (id: string) => !!bubbleBox.current.get(id)?.open;
  const shownFor = (a: LiveAgent) => ACTIVE.includes(a.state) ? SPEECH_SHOWN_S : SPEECH_AFTER_S;
  // Each person's page: it turns once its reading time is up and more has been said, one page per turn of the timer.
  const readings = useRef(new Map<string, Reading>());
  const pages = new Map<string, { page: string; more: boolean; turnAt: number }>();
  for (const a of feed.agents) {
    if (!a.speech) { readings.current.delete(a.id); continue; }
    const text = plainSpeech(a.speech), streaming = a.state === 'working' && now - a.speechAt < STREAM_S;
    let r = readings.current.get(a.id);
    // A different message (not the one on show, grown) starts reading from its beginning.
    if (!r || !text.startsWith(r.text.slice(0, r.end))) r = { text, start: 0, end: 0, at: now, readBy: now, streaming, seen: false };
    if (r.text !== text || r.streaming !== streaming || r.end === 0) {
      const end = pageEnd(a.short, text, r.start, r.end, streaming);
      r = { ...r, text, streaming, end, readBy: grown(r, text, end, now) };
    }
    const after = (t: string, i: number) => i + (t.slice(i).length - t.slice(i).trimStart().length);
    const next = after(text, r.end);
    // A page turns only while it is on show: one waiting for room is not read, so it must not advance unseen.
    if (next < text.length && now >= pageTurnAt(r) && onScreen.current.has(a.id)) {
      const start = text.length - next > PAGE_BACKLOG_CHARS ? catchUp(text) : next;
      const end = pageEnd(a.short, text, start, start, streaming);
      // Only turn to a page with words on it: what is left may be one word still streaming, held until it settles.
      if (end > start) r = { text, streaming, start, end, at: now, readBy: now + wordsReadS(text.slice(start, end)), seen: false };
    }
    readings.current.set(a.id, r);
    pages.set(a.id, { page: text.slice(r.start, r.end), more: after(text, r.end) < text.length, turnAt: pageTurnAt(r) });
  }
  // A new page remounts the closed bubble, so it enters with the bubble's fade-in. An open bubble keeps its key:
  // remounting it would drop the reader's hover, focus or pin.
  const bubbleKeys = useRef(new Map<string, string>());
  /** The page a person's closed bubble shows now: where it starts and when it opened. */
  const pageOf = (id: string) => { const r = readings.current.get(id); return r ? `${r.start}:${r.at}` : ''; };
  const pageKey = (id: string) => {
    if (!isOpen(id) && readings.current.has(id)) bubbleKeys.current.set(id, pageOf(id));
    return bubbleKeys.current.get(id) ?? id;
  };
  // A bubble stays while its person speaks, and until its last page has been read.
  const speaking = (a: LiveAgent) => {
    const p = pages.get(a.id);
    const r = readings.current.get(a.id);
    return !!p?.page && a.state !== 'stopped' && (now - a.speechAt < shownFor(a) || p.more || now < p.turnAt || (!!r && !r.seen && now - r.at < PAGE_WAIT_S));
  };
  // Neighbours who talk at once take turns by page (a4339a66). Placing newest speech first handed a shared spot to
  // whoever last added a word, so two bubbles swapped on every delta and one vanished mid-page. Open bubbles come
  // first, then any bubble on screen keeps its place until its page turns, then whoever has waited longest since they
  // were last on show, then the newest speech. Placement follows that order and stops once the cap's worth of bubbles
  // are drawn, so a page on show is never cut for a newer speaker, and one that fits nowhere (a cue) takes no slot.
  // Bubbles on show are placed in the order they came on show, each in the spot it already has while that stays
  // clear, so the layout they made is rebuilt as it was and none is pushed off.
  const holding = (id: string) => onScreen.current.get(id)?.page === pageOf(id);
  const shownLast = (id: string) => lastShown.current.get(id) ?? -Infinity;
  const onSince = (id: string) => onScreen.current.get(id)?.since ?? Infinity;
  const speakers = feed.agents.filter(a => pages.get(a.id)?.page && (isOpen(a.id) || speaking(a)))
    .sort((p, q) => Number(isOpen(q.id)) - Number(isOpen(p.id)) || Number(holding(q.id)) - Number(holding(p.id))
      || (holding(p.id) ? onSince(p.id) - onSince(q.id) : 0) || shownLast(p.id) - shownLast(q.id) || q.speechAt - p.speechAt);
  // A bubble leaves, or turns its page, when its time is up even if nothing else happens in the room: one timer, no loop.
  const [, setSpeechTick] = useState(0);
  // A word held back while it may still be streaming is released STREAM_S after the speech last changed, whether or
  // not a page is on show yet (a reply that so far is one word, "Done", has none): that release needs its own wake.
  const pendingWord = (a: LiveAgent) => !!readings.current.get(a.id)?.streaming;
  const nextSpeechEnd = Math.min(Infinity, ...feed.agents.flatMap(a => {
    const shown = speaking(a) && !isOpen(a.id), p = pages.get(a.id), r = readings.current.get(a.id);
    return [...(shown && p ? [a.speechAt + shownFor(a), p.turnAt] : []), ...(shown || pendingWord(a) ? [a.speechAt + STREAM_S] : []),
      ...(shown && r && !r.seen ? [r.at + PAGE_WAIT_S] : [])].filter(t => t > now);
  }));
  useEffect(() => {
    if (paused || !Number.isFinite(nextSpeechEnd)) return;
    const t = window.setTimeout(() => setSpeechTick(n => n + 1), Math.max(100, (nextSpeechEnd - Date.now() / 1000) * 1000 + 50));
    return () => window.clearTimeout(t);
  }, [nextSpeechEnd, paused]);
  // Stir: a working person's orbit and glow run while their work moves (a state change, a tool step, new reply
  // text) and for STIR_S after, then rest. Only real feed changes stir; one timer ends it, no frame loop.
  const stirAt = useRef(new Map<string, { key: string; at: number }>());
  for (const a of feed.agents) {
    const key = `${a.state}|${a.activity}|${a.speechAt}`, was = stirAt.current.get(a.id);
    if (was?.key !== key) stirAt.current.set(a.id, { key, at: now });
  }
  const stirring = (a: LiveAgent) => a.state === 'working' && now - (stirAt.current.get(a.id)?.at ?? 0) < STIR_S;
  const [, setStirTick] = useState(0);
  const nextStirEnd = Math.min(Infinity, ...feed.agents.filter(stirring).map(a => stirAt.current.get(a.id)!.at + STIR_S));
  useEffect(() => {
    if (paused || !Number.isFinite(nextStirEnd)) return;
    const t = window.setTimeout(() => setStirTick(n => n + 1), Math.max(100, (nextStirEnd - Date.now() / 1000) * 1000 + 50));
    return () => window.clearTimeout(t);
  }, [nextStirEnd, paused]);
  const bubbles: { agent: LiveAgent; rect: Rect | null; side?: BubbleSide; ring?: Point }[] = [];
  for (const id of [...heldSpot.current.keys()]) if (!isOpen(id) || !speakers.some(a => a.id === id)) heldSpot.current.delete(id);
  const cap = bubbleCap(size.w, size.h);
  for (const agent of speakers) {
    if (bubbles.filter(b => b.rect).length >= cap) break;
    // An open bubble keeps its spot (and grows from its anchored edge); the others step around it.
    const held = heldSpot.current.get(agent.id), box = bubbleBox.current.get(agent.id);
    if (held && box?.open) {
      // Opening grows the bubble from one edge. The direction that keeps every badge clear is chosen once,
      // when it opens, and kept while it stays open, so the reader's edge never jumps.
      // The open bubble may only take the free space in its column (never a badge, chip or another bubble):
      // it opens toward the side with more room, once, and scrolls inside beyond that room.
      const top = held.rect.y, bottom = held.rect.y + held.rect.h, x0 = held.rect.x, x1 = held.rect.x + held.rect.w;
      const others = [...blockers, ...bubbles.flatMap(o => o.rect ? [o.rect] : [])].filter(r => r.x < x1 && r.x + r.w > x0);
      const roomDown = Math.min(size.h, ...others.filter(r => r.y >= top + 1).map(r => r.y)) - top - 6;
      const roomUp = bottom - Math.max(0, ...others.filter(r => r.y + r.h <= bottom - 1).map(r => r.y + r.h)) - 6;
      if (!held.dir) held.dir = roomDown >= roomUp ? 'down' : 'up';
      const room = Math.max(held.rect.h, held.dir === 'down' ? roomDown : roomUp);
      openRoom.current.set(agent.id, room);
      const h = Math.min(box.h, room);
      bubbles.push({ agent, rect: held.dir === 'down' ? { x: x0, y: top, w: box.w, h } : { x: x0, y: bottom - h, w: box.w, h }, side: held.dir === 'up' ? 'above' : 'below' });
      continue;
    }
    const seat = layout.seats.get(agent.id); if (!seat) continue;
    // Full width beside the person, else narrower beside them, else the nearest clear spot a little farther out.
    const taken = [...blockers, ...bubbles.flatMap(b => b.rect ? [b.rect] : [])];
    // While its speaker works, a page may still fill (a word held back as possibly half-streamed counts as text past
    // it, so `more` doesn't mean the page is closed). It is placed at its full page height where that fits, so the
    // lines still to come never push it into a neighbour's spot (a4339a66); the bubble itself grows from its anchored
    // edge. On a crowded stage where the full height doesn't fit, a bubble already on show, or one whose words have
    // stopped arriving, is placed at the page's height now, each spot tried at both heights before the next spot out.
    // One still streaming waits for its full height: let in short, its next line would fit nowhere and it would vanish.
    const filling = agent.state === 'working', mayShrink = holding(agent.id) || !readings.current.get(agent.id)?.streaming;
    const sizes = (max: number) => {
      const e = bubbleSize(agent.short, pages.get(agent.id)!.page, max, BUBBLE_NARROW_LINES), s = box && box.w === e.w ? { w: box.w, h: box.h } : e;
      const full = { ...s, h: Math.max(s.h, (max === BUBBLE_NARROW ? BUBBLE_NARROW_LINES : BUBBLE_LINES) * BUBBLE_LINE + BUBBLE_PAD_Y) };
      return filling && full.h > s.h ? (mayShrink ? [full, s] : [full]) : [s];
    };
    const first = <T,>(sz: { w: number; h: number }[], at: (s: { w: number; h: number }) => T | null) => sz.reduce<T | null>((hit, s) => hit ?? at(s), null);
    // The narrow bubble only when the page still fits its lines there; otherwise it would clip the page's start.
    const narrow = bubbleSize(agent.short, pages.get(agent.id)!.page, BUBBLE_NARROW, BUBBLE_NARROW_LINES).lines <= BUBBLE_NARROW_LINES;
    // A page on show keeps its spot while that stays clear: its side beside the person, or, for one farther out, the
    // same offset from them (a fresh search could find a different spot first and make it hop or lose its place).
    const was = holding(agent.id) ? onScreen.current.get(agent.id) : undefined, ring = was?.ring;
    const keep = (s: { w: number; h: number }) => {
      const rect = ring ? { x: seat.x + ring.x - s.w / 2, y: seat.y + ring.y - s.h / 2, ...s } : null;
      return rect && bubbleClear(rect, taken, stage) ? { side: was!.side!, rect, ring: true as const } : null;
    };
    const spot = first(sizes(BUBBLE_MAX), keep)
      ?? first(sizes(BUBBLE_MAX), s => placeBubble(seat, s, taken, stage, was?.side))
      ?? (narrow ? first(sizes(BUBBLE_NARROW), s => placeBubble(seat, s, taken, stage, was?.side)) : null)
      ?? first(sizes(BUBBLE_MAX), s => placeBubble(seat, s, taken, stage, undefined, BUBBLE_REACH));
    if (spot) heldSpot.current.set(agent.id, { side: spot.side, rect: spot.rect });
    bubbles.push({ agent, rect: spot?.rect ?? null, side: spot?.side,
      ring: spot?.ring ? { x: spot.rect.x + spot.rect.w / 2 - seat.x, y: spot.rect.y + spot.rect.h / 2 - seat.y } : undefined });
  }
  // A page is seen only once it is actually drawn: a new page, or one coming back on show after waiting, starts its
  // reading time now. A page that turned but lost its spot stays unseen, keeps its PAGE_WAIT_S allowance and waits.
  for (const b of bubbles) {
    const r = b.rect && !isOpen(b.agent.id) ? readings.current.get(b.agent.id) : undefined;
    if (r && (!r.seen || !onScreen.current.has(b.agent.id))) readings.current.set(b.agent.id, { ...r, at: now, readBy: now + wordsReadS(r.text.slice(r.start, r.end)), seen: true });
  }
  // Who is on screen now, with the page they show (an open bubble always is; it is placed first anyway).
  onScreen.current = new Map(bubbles.filter(b => b.rect).map(b => [b.agent.id,
    { page: pageOf(b.agent.id), since: holding(b.agent.id) ? onSince(b.agent.id) : now, side: b.side, ring: b.ring }]));
  for (const id of onScreen.current.keys()) lastShown.current.set(id, now);
  for (const id of [...lastShown.current.keys()]) if (!feed.agents.some(a => a.id === id)) lastShown.current.delete(id);

  const groupOf = new Map<string, CollabGroup>([...groups.values()].map(g => [g.key, g]));
  const short = (id: string) => id === 'user' ? 'You' : id === 'team' ? 'everyone' : feed.agents.find(a => a.id === id)?.short || id;
  const latest = rail ? railItems(feed).slice(0, railRows) : [];
  return <div ref={root} className={`lv-stage${paused ? ' lv-paused' : ''}`} aria-label="The team right now">
    <canvas ref={canvas} className="lv-effects" aria-hidden="true" />
    {layout.formations.map(f => <div key={`floor-${f.group}`} className="lv-floor" aria-hidden="true"
      style={{ left: f.box.x - 14, top: f.box.y + LABEL_H - 8, width: f.box.w + 28, height: f.box.h - LABEL_H + 16, '--c': feed.agents.find(a => a.id === f.members[0])?.color } as CSSProperties} />)}
    {layout.formations.map(f => {
      const g = groupOf.get(f.group), anchor = g?.anchor;
      const why = g?.why === 'review' ? 'review' : g?.why === 'followup' ? 'follow-up' : g?.why === 'recent' ? 'talking' : '';
      return <button key={f.group} type="button" className={`lv-chip${selectedTask === f.group || (anchor && selectedPerson === anchor) ? ' lv-selected' : ''}`}
        style={{ left: f.box.x + f.box.w / 2, top: f.box.y + LABEL_H - 4, maxWidth: f.box.w }}
        onClick={() => anchor ? onSelectPerson(anchor) : onFollowWork(f.group)}
        title={anchor ? 'Follow this person: who they are talking with and what they said' : 'Follow this work: owner, conversation and evidence in one panel'}>
        <span aria-hidden="true">◆</span><b>{g ? chipText(g, feed) : 'Shared task'}</b><small>· {why ? `${why} · ` : ''}{f.members.length}</small>
      </button>;
    })}
    {feed.agents.map(a => {
      const quiet = !groups.has(a.id) && (a.state === 'idle' || a.state === 'stopped');
      const bubble = bubbles.find(b => b.agent.id === a.id);
      return <button key={a.id} type="button" ref={el => { if (el) nodes.current.set(a.id, el); else nodes.current.delete(a.id); }}
        className={`lv-person lv-${a.state}${quiet ? ' lv-quiet' : ''}${stirring(a) ? ' lv-stir' : ''}${selectedPerson === a.id ? ' lv-selected' : ''}`}
        style={{ '--c': a.color, ...rhythm(a.id) } as CSSProperties}
        onClick={() => onSelectPerson(a.id)}
        aria-label={`${a.name}: ${stateWords(a)}${a.task_title ? `, on ${a.task_title}` : ''}${bubble && a.speech ? `. Says: ${a.speech}` : ''}`}>
        <span className="lv-float">
          <span className="lv-badge"><span className="lv-orbit" aria-hidden="true" /><span className="lv-ring" aria-hidden="true" />{initials(a.short)}</span>
          <span className="lv-name" title={a.name}>{a.short}</span>
          <span className="lv-doing">{stateWords(a)}</span>
          {a.artifact && !quiet && layout.thumbs && (a.artifact.kind === 'browser_frame'
            ? <span className="lv-thumb lv-thumb-live" aria-hidden="true">● browser</span>
            : isVideo(a.artifact.url) ? <span key={a.artifact.url} className="lv-thumb lv-thumb-live" aria-hidden="true">▶ video</span>
            : <img key={a.artifact.url} className="lv-thumb" src={a.artifact.url} alt="" loading="lazy" />)}
          {bubble && !bubble.rect && <span className="lv-cue" title={a.speech} aria-hidden="true">💬</span>}
        </span>
      </button>;
    })}
    {rail && <section className="lv-rail" aria-label="Latest changes" style={{ left: rail.x, bottom: size.h - rail.y - rail.h, width: rail.w, maxHeight: rail.h }}>
      <h2>Latest</h2>
      {latest.length ? <ol>{latest.map(i => <li key={i.id} className={i.fresh ? 'lv-rail-fresh' : undefined}>
        <button type="button" style={{ '--c': feed.agents.find(a => a.id === i.from)?.color || '#f0c674' } as CSSProperties}
          onClick={() => i.task_id ? onFollowWork(i.task_id) : onSelectPerson(i.from === 'user' ? i.to : i.from)}
          title={i.text || undefined}>
          <i aria-hidden="true" /><span>{railWords(i, short)}</span>{i.task_title && <small>{i.task_title}</small>}
        </button>
      </li>)}</ol> : <p>Nothing has changed since you opened Live.</p>}
    </section>}
    {bubbles.filter(b => b.rect).map(b => {
      // A bubble above its person is anchored by its bottom edge, so it grows upward, away from the badge.
      const up = b.side === 'above' || b.side === 'upLeft' || b.side === 'upRight';
      // A bubble beside its person sits centred on them inside its spot, which may be reserved taller (filling).
      const beside = b.side === 'right' || b.side === 'left', shownH = bubbleBox.current.get(b.agent.id)?.h ?? b.rect!.h;
      const slot: CSSProperties = up ? { left: b.rect!.x, bottom: size.h - (b.rect!.y + b.rect!.h) }
        : { left: b.rect!.x, top: b.rect!.y + (beside ? Math.max(0, (b.rect!.h - shownH) / 2) : 0) };
      return <div key={b.agent.id} className="lv-bubble-slot" style={slot}>
        <SpeechBubble key={pageKey(b.agent.id)} name={b.agent.short} color={b.agent.color} text={isOpen(b.agent.id) ? b.agent.speech : pages.get(b.agent.id)!.page}
          width={b.rect!.w} maxLines={BUBBLE_NARROW_LINES} reduced={reduced} openMaxHeight={openRoom.current.get(b.agent.id)}
          onSize={box => {
            const prev = bubbleBox.current.get(b.agent.id);
            if (prev && prev.w === box.w && Math.abs(prev.h - box.h) <= 1 && prev.open === box.open) return;
            bubbleBox.current.set(b.agent.id, box); setBubbleTick(n => n + 1);
          }} />
      </div>;
    })}
  </div>;
}

function effectFor(i: LiveInteraction, color: (id: string) => string, name: (id: string) => string): Effect | null {
  const base = { from: i.from, to: i.to, color: color(i.from), start: 0 };
  switch (i.kind) {
    case 'mention': return i.to === 'team' ? { ...base, kind: 'broadcast', label: '@all', dur: 1400 } : { ...base, kind: 'comet', label: `@${name(i.to)}`, dur: 900 };
    case 'handoff': return !i.from_owner || i.from_owner === 'unassigned' ? null : { ...base, from: i.from_owner || i.from, to: i.to_owner || i.to, kind: 'baton', label: i.task_title ? clipLabel(i.task_title) : 'handoff', dur: 1200 };
    case 'review_request': return { ...base, kind: 'comet', label: 'review?', dur: 900 };
    case 'review_verdict': return { ...base, kind: 'comet', label: i.text.startsWith('Approved') ? 'approved ✓' : 'changes', dur: 900 };
    case 'followup': return { ...base, kind: 'comet', label: 'follow-up', dur: 900 };
    case 'followup_result': return { ...base, kind: 'comet', label: 'done ✓', dur: 900 };
    case 'question': return { ...base, to: 'user', kind: 'gold', label: 'asks you', dur: 1100 };
    case 'answer': return { ...base, from: 'user', kind: 'answer', label: 'answered', dur: 900 };
    default: return null;
  }
}
const clipLabel = (t: string) => t.length > 26 ? t.slice(0, 25) + '…' : t;

/** The relations that gather people: pending reviews and open follow-ups from the board, and exchanges between two
 * teammates (an @mention, a verdict, a follow-up answer, a handoff) for two minutes after they happen. */
function collabRelations(feed: LiveFeed, now: number): CollabLink[] {
  const ids = new Set(feed.agents.map(a => a.id)), cutoff = now - LINK_HOLD_S.recent;
  const recent = feed.interactions.filter(i => i.at >= cutoff && ids.has(i.from) && ids.has(i.to)
    && (i.kind === 'mention' || i.kind === 'review_verdict' || i.kind === 'followup_result' || i.kind === 'handoff'))
    .map(i => ({ kind: 'recent' as const, from: i.from, to: i.to, task_id: i.task_id, task_title: i.task_title, at: i.at }));
  return [...feed.links.map(l => ({ kind: l.kind, from: l.from, to: l.to, task_id: l.task_id, task_title: l.task_title, at: l.at })), ...recent];
}

/** A group's chip: the task's title, or who the conversation is with. */
function chipText(g: CollabGroup, feed: LiveFeed): string {
  if (g.anchor) return `With ${feed.agents.find(a => a.id === g.anchor)?.short || g.anchor}`;
  return g.title || feed.agents.find(a => a.task_id === g.key)?.task_title || feed.links.find(l => l.task_id === g.key)?.task_title || 'Shared task';
}

type RailItem = Pick<LiveInteraction, 'id' | 'at' | 'from' | 'to' | 'task_id' | 'task_title' | 'text' | 'fresh'> & { kind: LiveInteraction['kind'] | 'evidence'; evidence?: string };
/** What changed, newest first: the room's interactions plus each person's latest picture. */
function railItems(feed: LiveFeed): RailItem[] {
  const title = (task?: string) => task ? feed.agents.find(x => x.task_id === task)?.task_title || feed.links.find(l => l.task_id === task)?.task_title : undefined;
  const pictures: RailItem[] = feed.agents.filter(a => a.artifact && a.artifact.kind !== 'browser_frame').map(a => ({
    id: `evidence:${a.id}:${a.artifact!.url}`, at: a.artifact!.at, from: a.id, to: '', kind: 'evidence', evidence: a.artifact!.kind,
    task_id: a.artifact!.task_id, task_title: title(a.artifact!.task_id), text: a.artifact!.note || '' }));
  return [...feed.interactions.filter(i => i.kind !== 'decision_turn'), ...pictures].sort((p, q) => q.at - p.at);
}
function railWords(i: RailItem, name: (id: string) => string): string {
  switch (i.kind) {
    case 'mention': return `${name(i.from)} mentioned ${name(i.to)}`;
    case 'handoff': return `${name(i.from)} handed over to ${name(i.to)}`;
    case 'review_request': return `${name(i.from)} asked ${name(i.to)} for a review`;
    case 'review_verdict': return `${name(i.from)} ${i.text.startsWith('Approved') ? 'approved' : 'requested changes'} for ${name(i.to)}`;
    case 'followup': return `${name(i.from)} asked ${name(i.to)} to follow up`;
    case 'followup_result': return `${name(i.from)} answered ${name(i.to)}'s follow-up`;
    case 'question': return `${name(i.from)} asked you`;
    case 'answer': return `You answered ${name(i.to)}`;
    case 'evidence': return `${name(i.from)} shared ${i.evidence === 'markup' ? 'an annotation' : 'a picture'}`;
    default: return `${name(i.from)} and ${name(i.to)}`;
  }
}

export function stateWords(a: LiveAgent): string {
  if (a.state === 'waiting_on_user') return a.activity || 'waiting for you';
  if (a.state === 'failed') return a.reason || 'turn failed';
  if (a.state === 'stopped') return 'stopped';
  if (a.state === 'idle') return 'available';
  return a.activity || 'working';
}
