/* The Live stage (task b42437b9): the team as boxless people. Each motion means one thing:
 * gathering = working on the same task, comet = @mention, baton = handoff, gold rise = a question
 * for you, ring step = a tool ran, one head shake = a failed turn. People are buttons (keyboard
 * reachable); effects are drawn on an aria-hidden canvas and always have a plain-text twin. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { isVideo, type LiveAgent, type LiveFeed, type LiveInteraction } from './liveFeed';
import { LABEL_H, layoutStage, personRect, placeBubble, springStep, type BubbleSide, type Point, type Rect } from './liveLayout';

const MAX_MOVERS = 3;          // at most three people travel at once; the rest wait their turn
const MAX_EFFECTS = 3;         // at most three comets or batons in the air
const SPEECH_SHOWN_S = 9;
const BUBBLE = { w: 230, h: 64 };
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

interface Body { x: number; y: number; vx: number; vy: number; tx: number; ty: number; waitingSince: number; shakeAt: number; hopAt: number; ring: number; phase: number }
interface Effect { kind: 'comet' | 'baton' | 'gold' | 'answer' | 'broadcast'; from: string; to: string; label: string; color: string; start: number; dur: number; landed?: boolean }

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
const short = (name: string) => name.length > 14 ? name.split(/\s+/)[0] : name;

export function LiveStage({ feed, reduced, paused, youArea, selectedPerson, selectedTask, onSelectPerson, onFollowWork }: StageProps) {
  const root = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 900, h: 560 });
  const bodies = useRef(new Map<string, Body>());
  const nodes = useRef(new Map<string, HTMLButtonElement>());
  const effects = useRef<Effect[]>([]), queued = useRef<Effect[]>([]);
  const seen = useRef(new Set<string>());
  const prevState = useRef(new Map<string, string>()), prevActivity = useRef(new Map<string, string>());
  const joined = useRef(new Map<string, { group: string; at: number }>());
  const [hover, setHover] = useState<string | null>(null);
  const live = useRef({ paused, reduced, hover }); live.current = { paused, reduced, hover };
  const clock = useRef(0);   // animation time in ms; frozen while paused

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
      if (!b) { b = { x: seat.x, y: seat.y, vx: 0, vy: 0, tx: seat.x, ty: seat.y, waitingSince: 0, shakeAt: -1e9, hopAt: -1e9, ring: 0, phase: [...a.id].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 6283, 7) / 1000 }; bodies.current.set(a.id, b); }
      if (b.tx !== seat.x || b.ty !== seat.y) { b.tx = seat.x; b.ty = seat.y; b.waitingSince = t; }
      if (a.state === 'failed' && prevState.current.get(a.id) && prevState.current.get(a.id) !== 'failed') b.shakeAt = t;
      if (a.activity && prevActivity.current.has(a.id) && prevActivity.current.get(a.id) !== a.activity) b.ring += 60;
      prevState.current.set(a.id, a.state); prevActivity.current.set(a.id, a.activity);
    }
    for (const id of [...bodies.current.keys()]) if (!layout.seats.has(id)) bodies.current.delete(id);
  }, [feed.agents, layout]);

  // New interactions become effects (history from the snapshot never animates).
  useEffect(() => {
    const color = (id: string) => feed.agents.find(a => a.id === id)?.color || '#54d9ed';
    const name = (id: string) => id === 'user' ? 'you' : id === 'team' ? 'everyone' : short(feed.agents.find(a => a.id === id)?.name || id);
    for (const i of [...feed.interactions].reverse()) {
      if (seen.current.has(i.id)) continue;
      seen.current.add(i.id);
      if (!i.fresh) continue;
      const e = effectFor(i, color, name);
      if (e) queued.current.push(e);
    }
  }, [feed.interactions, feed.agents]);

  // Animation loop: springs toward seats (three movers at most), bob, shake, effects on canvas.
  useEffect(() => {
    let raf = 0, last = performance.now();
    const ctx = canvas.current?.getContext('2d');
    const frame = (now: number) => {
      const real = Math.min(50, now - last); last = now;
      const { paused, reduced, hover } = live.current;
      // One animation clock for motion, effects, bob, shake and hop. It stands still while paused, so
      // effects freeze exactly and nothing snaps on resume, at any refresh rate.
      if (!paused) clock.current += real;
      const t = clock.current, dt = paused ? 0 : real / 1000;
      if (!paused) {
        const moving = [...bodies.current.values()].filter(b => Math.hypot(b.tx - b.x, b.ty - b.y) > 2);
        const allowed = new Set(reduced ? moving : moving.sort((p, q) => p.waitingSince - q.waitingSince).slice(0, MAX_MOVERS));
        for (const b of bodies.current.values()) {
          if (reduced) { b.x = b.tx; b.y = b.ty; b.vx = b.vy = 0; continue; }
          if (!allowed.has(b) && Math.hypot(b.tx - b.x, b.ty - b.y) > 2) continue;
          [b.x, b.vx] = springStep(b.x, b.vx, b.tx, dt); [b.y, b.vy] = springStep(b.y, b.vy, b.ty, dt);
        }
        while (effects.current.length < MAX_EFFECTS && queued.current.length) effects.current.push({ ...queued.current.shift()!, start: t });
      }
      for (const [id, el] of nodes.current) {
        const b = bodies.current.get(id); if (!b) continue;
        let x = b.x, y = b.y;
        // Offsets come from the frozen clock while paused, so pausing holds people exactly where they are.
        if (!reduced) {
          if (hover !== id) y += Math.sin(t / 650 + b.phase) * 2.5;
          const s = (t - b.shakeAt) / 480; if (s >= 0 && s < 1) x += Math.sin(s * 34) * 7 * (1 - s);
          const h = (t - b.hopAt) / 320; if (h >= 0 && h < 1) y -= 8 * Math.sin(Math.PI * h);
        }
        el.style.transform = `translate(${x}px, ${y}px)`;
        el.style.setProperty('--ring', `${b.ring}deg`);
      }
      if (ctx && canvas.current) drawEffects(ctx, canvas.current, t, paused, reduced);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
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
    effects.current = effects.current.filter(e => (t - e.start) < e.dur + 1700);
  }
  function land(e: Effect, t: number) { const b = bodies.current.get(e.to); if (b) b.hopAt = t; }

  // Speech: newest three active speakers, each in a clear spot or as a cue by the person.
  const now = Date.now() / 1000;
  const blockers: Rect[] = [youArea, ...layout.formations.map(f => ({ ...f.box, h: 22 })),
    ...feed.agents.map(a => { const s = layout.seats.get(a.id); return s ? personRect(s, a.state === 'idle' || a.state === 'stopped', !!a.artifact) : null; }).filter((r): r is Rect => !!r)];
  const speakers = feed.agents.filter(a => a.speech && now - a.speechAt < SPEECH_SHOWN_S && ACTIVE.includes(a.state)).sort((p, q) => q.speechAt - p.speechAt).slice(0, 3);
  const bubbles: { agent: LiveAgent; rect: Rect | null; side?: BubbleSide }[] = [];
  for (const agent of speakers) {
    const seat = layout.seats.get(agent.id); if (!seat) continue;
    const spot = placeBubble(seat, BUBBLE, [...blockers, ...bubbles.flatMap(b => b.rect ? [b.rect] : [])], stage);
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
        onClick={() => onSelectPerson(a.id)} onPointerEnter={() => setHover(a.id)} onPointerLeave={() => setHover(h => h === a.id ? null : h)}
        aria-label={`${a.name}: ${stateWords(a)}${a.task_title ? `, on ${a.task_title}` : ''}${bubble && a.speech ? `. Says: ${a.speech}` : ''}`}>
        <span className="lv-badge"><span className="lv-ring" aria-hidden="true" />{initials(a.name)}</span>
        <span className="lv-name">{short(a.name)}</span>
        <span className="lv-doing">{stateWords(a)}</span>
        {a.artifact && !quiet && (a.artifact.kind === 'browser_frame'
          ? <span className="lv-thumb lv-thumb-live" aria-hidden="true">● browser</span>
          : isVideo(a.artifact.url) ? <span className="lv-thumb lv-thumb-live" aria-hidden="true">▶ video</span>
          : <img className="lv-thumb" src={a.artifact.url} alt="" loading="lazy" />)}
        {bubble && !bubble.rect && <span className="lv-cue" title={a.speech} aria-hidden="true">💬</span>}
      </button>;
    })}
    {bubbles.filter(b => b.rect).map(b => <div key={b.agent.id} className="lv-bubble" aria-hidden="true"
      style={{ left: b.rect!.x, top: b.rect!.y, width: b.rect!.w, '--c': b.agent.color } as CSSProperties}>
      <b>{short(b.agent.name)}</b> {b.agent.speech}
    </div>)}
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
