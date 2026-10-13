/* The Live stage (task b42437b9): the team as boxless people. Each motion means one thing:
 * gathering = working on the same task, comet = @mention, baton = handoff, gold rise = a question
 * for you, ring step = a tool ran, one head shake = a failed turn. People are buttons (keyboard
 * reachable); effects are drawn on an aria-hidden canvas and always have a plain-text twin. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { isVideo, type LiveAgent, type LiveFeed, type LiveInteraction } from './liveFeed';
import { plainSpeech, SpeechBubble } from '../components/SpeechBubble';
import { LABEL_H, layoutStage, overlap, placeBubble, speechBlocker, springStep, type BubbleSide, type Point, type Rect } from './liveLayout';

const MAX_MOVERS = 3;          // at most three people travel at once; the rest wait their turn
const MAX_EFFECTS = 3;         // at most three comets or batons in the air
// The animation loop runs only while something moves (Zero 0bb2bdcf: an always-on loop kept a CPU core busy).
const MAX_FRAME_MS = 50;       // a frame never advances motion further than this, however late it runs
const SETTLE = { px: 0.25, pxPerS: 1 };       // a spring this close and this slow is at rest: snap it and stop
const SHAKE = { ms: 480, cycles: 34, px: 7 }; // one head shake after a failed turn
const HOP = { ms: 320, px: 8 };               // the small hop when a comet or baton lands
const EFFECT_TAIL_MS = 1700;   // an effect stays drawn this long after it lands (ring, fade)
const SPEECH_SHOWN_S = 9;
// Speech bubbles are sized to their text (Zero #14496: bubbles were cut off), measured before placement so
// overlap avoidance uses the real size, then resized with a soft ease as the text grows or a shorter
// message replaces a longer one.
const BUBBLE_FONT = '12.5px Inter, "Segoe UI", system-ui, sans-serif', BUBBLE_NAME_FONT = '600 ' + BUBBLE_FONT;
const BUBBLE_LINE = 17, BUBBLE_PAD_X = 23, BUBBLE_PAD_Y = 12, BUBBLE_MIN = 120, BUBBLE_MAX = 280, BUBBLE_LINES = 5;
let measureCtx: CanvasRenderingContext2D | null = null;
export function bubbleSize(name: string, text: string): { w: number; h: number } {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d');
  const ctx = measureCtx!;
  const inner = BUBBLE_MAX - BUBBLE_PAD_X;
  ctx.font = BUBBLE_NAME_FONT; let x = ctx.measureText(name + ' ').width;
  ctx.font = BUBBLE_FONT;
  const space = ctx.measureText(' ').width;
  let lines = 1, widest = x;
  for (const word of plainSpeech(text).split(/\s+/).filter(Boolean)) {
    const w = ctx.measureText(word).width;
    if (x > 0 && x + w > inner) { lines++; widest = Math.max(widest, x - space); x = 0; }
    x += Math.min(w, inner) + space;
  }
  widest = Math.max(widest, x - space);
  return { w: Math.ceil(Math.min(BUBBLE_MAX, Math.max(BUBBLE_MIN, widest + BUBBLE_PAD_X + 2))), h: Math.min(lines, BUBBLE_LINES) * BUBBLE_LINE + BUBBLE_PAD_Y };
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
interface Effect { kind: 'comet' | 'baton' | 'gold' | 'answer' | 'broadcast'; from: string; to: string; label: string; color: string; start: number; dur: number; landed?: boolean }

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

  // Group membership: people actively on the same task gather; the time they joined fixes their seat order.
  const people = useMemo(() => feed.agents.map(a => {
    const group = ACTIVE.includes(a.state) && a.task_id ? a.task_id : '';
    const j = joined.current.get(a.id);
    if (!j || j.group !== group) joined.current.set(a.id, { group, at: Date.now() });
    return { id: a.id, order: a.order, group, label: group ? `◆ ${a.task_title || 'Shared task'} · 00` : undefined, joinedAt: joined.current.get(a.id)!.at, quiet: a.state === 'idle' || a.state === 'stopped', hasThumb: !!a.artifact };
  }), [feed.agents]);
  const stage: Rect = { x: 0, y: 0, w: size.w, h: size.h };
  const spots = useRef(new Map<string, Rect>());
  const layout = useMemo(() => {
    const next = layoutStage(people, stage, [youArea], spots.current);
    spots.current = new Map(next.formations.map(f => [f.group, f.box]));
    return next;
  }, [people, size.w, size.h, youArea.x, youArea.y, youArea.w, youArea.h]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seats and per-person events (state change shake, tool ring step).
  useEffect(() => {
    const t = clock.current;
    for (const a of feed.agents) {
      const seat = layout.seats.get(a.id); if (!seat) continue;
      let b = bodies.current.get(a.id);
      if (!b) { b = { x: seat.x, y: seat.y, vx: 0, vy: 0, tx: seat.x, ty: seat.y, waitingSince: 0, shakeAt: -1e9, hopAt: -1e9, ring: 0 }; bodies.current.set(a.id, b); }
      if (b.tx !== seat.x || b.ty !== seat.y) { b.tx = seat.x; b.ty = seat.y; b.waitingSince = t; }
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
      if (e) queued.current.push(e);
    }
    if (queued.current.length) kick.current();
  }, [feed.interactions, feed.agents]);

  // Pausing freezes the clock and lets the loop rest; resuming or switching reduced motion restarts it.
  useEffect(() => { kick.current(); }, [paused, reduced]);

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

  // Speech: newest three active speakers, each in a clear spot or as a cue by the person.
  const now = Date.now() / 1000;
  const blockers: Rect[] = [youArea, ...layout.formations.map(f => ({ ...f.box, h: 22 })),
    ...feed.agents.map(a => { const s = layout.seats.get(a.id); return s ? speechBlocker(s, a.state === 'idle' || a.state === 'stopped', !!a.artifact) : null; }).filter((r): r is Rect => !!r)];
  const isOpen = (id: string) => !!bubbleBox.current.get(id)?.open;
  const speakers = feed.agents.filter(a => a.speech && (isOpen(a.id) || (now - a.speechAt < SPEECH_SHOWN_S && ACTIVE.includes(a.state))))
    .sort((p, q) => Number(isOpen(q.id)) - Number(isOpen(p.id)) || q.speechAt - p.speechAt).slice(0, 3);
  const bubbles: { agent: LiveAgent; rect: Rect | null; side?: BubbleSide }[] = [];
  for (const id of [...heldSpot.current.keys()]) if (!isOpen(id) || !speakers.some(a => a.id === id)) heldSpot.current.delete(id);
  for (const agent of speakers) {
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
    const estimate = bubbleSize(agent.short, agent.speech);
    const boxSize = box && box.w === estimate.w ? { w: box.w, h: box.h } : estimate;
    const spot = placeBubble(seat, boxSize, [...blockers, ...bubbles.flatMap(b => b.rect ? [b.rect] : [])], stage);
    if (spot) heldSpot.current.set(agent.id, { ...spot });
    bubbles.push({ agent, rect: spot?.rect ?? null, side: spot?.side });
  }

  const taskTitle = (id: string) => feed.agents.find(a => a.task_id === id)?.task_title || 'Shared task';
  return <div ref={root} className="lv-stage" aria-label="The team right now">
    <canvas ref={canvas} className="lv-effects" aria-hidden="true" />
    {layout.formations.map(f => <button key={f.group} type="button" className={`lv-chip${selectedTask === f.group ? ' lv-selected' : ''}`}
      style={{ left: f.box.x + f.box.w / 2, top: f.box.y + LABEL_H - 4, maxWidth: f.box.w }} onClick={() => onFollowWork(f.group)}
      title="Follow this work: owner, conversation and evidence in one panel">
      <span aria-hidden="true">◆</span> {taskTitle(f.group)} <small>· {f.members.length}</small>
    </button>)}
    {feed.agents.map(a => {
      const quiet = a.state === 'idle' || a.state === 'stopped';
      const bubble = bubbles.find(b => b.agent.id === a.id);
      return <button key={a.id} type="button" ref={el => { if (el) nodes.current.set(a.id, el); else nodes.current.delete(a.id); }}
        className={`lv-person lv-${a.state}${quiet ? ' lv-quiet' : ''}${selectedPerson === a.id ? ' lv-selected' : ''}`}
        style={{ '--c': a.color } as CSSProperties}
        onClick={() => onSelectPerson(a.id)}
        aria-label={`${a.name}: ${stateWords(a)}${a.task_title ? `, on ${a.task_title}` : ''}${bubble && a.speech ? `. Says: ${a.speech}` : ''}`}>
        <span className="lv-badge"><span className="lv-ring" aria-hidden="true" />{initials(a.short)}</span>
        <span className="lv-name" title={a.name}>{a.short}</span>
        <span className="lv-doing">{stateWords(a)}</span>
        {a.artifact && !quiet && (a.artifact.kind === 'browser_frame'
          ? <span className="lv-thumb lv-thumb-live" aria-hidden="true">● browser</span>
          : isVideo(a.artifact.url) ? <span className="lv-thumb lv-thumb-live" aria-hidden="true">▶ video</span>
          : <img className="lv-thumb" src={a.artifact.url} alt="" loading="lazy" />)}
        {bubble && !bubble.rect && <span className="lv-cue" title={a.speech} aria-hidden="true">💬</span>}
      </button>;
    })}
    {bubbles.filter(b => b.rect).map(b => {
      // A bubble above its person is anchored by its bottom edge, so it grows upward, away from the badge.
      const up = b.side === 'above' || b.side === 'upLeft' || b.side === 'upRight';
      const slot: CSSProperties = up ? { left: b.rect!.x, bottom: size.h - (b.rect!.y + b.rect!.h) } : { left: b.rect!.x, top: b.rect!.y };
      return <div key={b.agent.id} className="lv-bubble-slot" style={slot}>
        <SpeechBubble name={b.agent.short} color={b.agent.color} text={b.agent.speech} width={b.rect!.w} reduced={reduced} openMaxHeight={openRoom.current.get(b.agent.id)}
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

export function stateWords(a: LiveAgent): string {
  if (a.state === 'waiting_on_user') return a.activity || 'waiting for you';
  if (a.state === 'failed') return a.reason || 'turn failed';
  if (a.state === 'stopped') return 'stopped';
  if (a.state === 'idle') return 'available';
  return a.activity || 'working';
}
