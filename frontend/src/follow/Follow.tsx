/* Follow the current executing task from the server, with explicit event links taking priority. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { sessionTitle } from '../lib/room';
import type { RunInfo, Task } from '../lib/types';
import { type Agents } from '../board/shared';
import { STATUS_LABELS } from '../board/shared';
import { reconcileRuns, setFollowing, setPaused, useFollow, type AgentLive, type FollowSnapshot, type TaskMove } from './followStore';
import './follow.css';
import { SpeechBubble } from '../components/SpeechBubble';

/** What follow mode adds to one task card. */
export interface CardFollow {
  live?: AgentLive;
  move?: TaskMove;
}

export interface FollowPlacement {
  cards: Map<string, CardFollow>;
  /** Live agents without a unique executing task remain in the banner. */
  unplaced: AgentLive[];
}

export function placeFollow(snap: FollowSnapshot, tasks: Task[]): FollowPlacement {
  const cards = new Map<string, CardFollow>();
  const unplaced: AgentLive[] = [];
  if (!snap.following) return { cards, unplaced };
  for (const entry of Object.values(snap.live)) {
    const explicit = entry.task && tasks.find(t => t.id === entry.task);
    const executing = tasks.filter(t => t.owner === entry.agent && t.execution_state === 'executing');
    const linked = explicit || (executing.length === 1 ? executing[0] : undefined);
    if (linked) cards.set(linked.id, { live: entry });
    else unplaced.push(entry);
  }
  for (const [id, move] of Object.entries(snap.moves)) cards.set(id, { ...cards.get(id), move });
  return { cards, unplaced };
}

export function followName(name: string, agents: Agents): string {
  const handle = agents[name]?.handle || name; return handle.charAt(0).toUpperCase() + handle.slice(1);
}

function agentColor(name: string, agents: Agents): string {
  return agents[name]?.color || 'var(--text-muted)';
}

function Avatar({ name, agents }: { name: string; agents: Agents }) {
  const label = followName(name, agents);
  return <span className="fl-avatar" style={{ '--agent-color': agentColor(name, agents) } as React.CSSProperties} aria-hidden="true">
    {label.slice(0, 2).toUpperCase()}
  </span>;
}

/** Header pill: turn follow on (and go to the Board) or off. */
export function FollowToggle({ onStart }: { onStart: () => void }) {
  const { following, paused } = useFollow();
  const label = !following ? 'Follow team' : paused ? 'Following paused' : 'Following team';
  return <button type="button" className={`fl-toggle${following ? ' is-on' : ''}${paused ? ' is-paused' : ''}`} aria-pressed={following}
    title={following ? 'Stop following (Esc)' : 'Watch the team work live on the Board'}
    onClick={() => { if (following) setFollowing(false); else { setFollowing(true); onStart(); } }}>
    <span className="fl-toggle-dot" aria-hidden="true" />
    {label}
  </button>;
}

function activity(entry: AgentLive): string {
  if (entry.tool && entry.tool.time >= entry.updated - 1) return entry.tool.label ? `is ${entry.tool.label}` : 'is using a tool';
  if (entry.text) return `is writing in ${sessionTitle(entry.channel)}`;
  return `started a turn in ${sessionTitle(entry.channel)}`;
}

/** Board banner while following: who is live, the commentary of agents not pinned to a card, Pause and Stop. */
export function FollowBanner({ unplaced, agents }: { unplaced: AgentLive[]; agents: Agents }) {
  const snap = useFollow();
  const banner = useRef<HTMLElement>(null);
  // Starting Follow leaves focus on the header toggle, where Space would press the button and stop
  // following. Move focus to the banner instead, so Space pauses and Esc stops as advertised; only
  // from the toggle itself, never taking focus from anything else.
  useEffect(() => {
    if (snap.following && document.activeElement?.closest?.('.fl-toggle')) banner.current?.focus({ preventScroll: true });
  }, [snap.following]);
  if (!snap.following) return null;
  const all = Object.values(snap.live);
  return <section ref={banner} tabIndex={-1} className={`fl-banner${snap.paused ? ' is-paused' : ''}`}
    aria-label="Following the team. Space pauses, Escape stops.">
    <div className="fl-banner-row">
      <p className="fl-banner-who" aria-live="polite">
        {snap.paused && <strong className="fl-paused">Paused · </strong>}
        {all.length === 0 ? 'Nobody is running a turn right now. Cards light up as agents start work.'
          : all.map((entry, i) => <span key={entry.agent} className="fl-who">
            {i > 0 && ' · '}
            <Avatar name={entry.agent} agents={agents} />
            <strong>{followName(entry.agent, agents)}</strong> {activity(entry)}
          </span>)}
      </p>
      <span className="fl-banner-hint">Shortcuts work outside text fields</span>
      <button type="button" className="fl-btn" onClick={() => setPaused(!snap.paused)}>{snap.paused ? 'Resume' : 'Pause'} · Space</button>
      <button type="button" className="fl-btn" onClick={() => setFollowing(false)}>Stop · Esc</button>
    </div>
    {unplaced.filter(e => e.text).sort((a,b)=>b.updated-a.updated).slice(0, 3).map(entry => <div key={entry.agent}>
      <CardSpeech name={followName(entry.agent, agents)} color={agentColor(entry.agent, agents)} text={entry.text} />
      <small>Not tied to one task</small>
    </div>)}
  </section>;
}

function clock(time: number): string {
  return new Date(time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Header strip of actual recent events. Native scrolling keeps every task link reachable.
 * Motion freezes on Pause, hover, keyboard focus, background tabs and reduced motion. */
export function FollowTicker({ tasks, agents, onOpenTask }: { tasks: Task[]; agents: Agents; onOpenTask: (id: string) => void }) {
  const snap = useFollow();
  const titles = useMemo(() => new Map(tasks.map(t => [t.id, t.title])), [tasks]);
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = viewport.current;
    if (!node || !snap.following || snap.paused) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let previous = 0;
    let position = node.scrollLeft;
    let holdUntil = 0;
    const move = (time: number) => {
      const held = reduced.matches || document.hidden || node.matches(':hover, :focus-within');
      const elapsed = previous ? Math.min(time - previous, 64) : 0;
      previous = time;
      const end = node.scrollWidth - node.clientWidth;
      if (held) position = node.scrollLeft;
      else if (end > 0 && time >= holdUntil) {
        position += elapsed * 0.032;
        if (position >= end) {
          position = 0;
          holdUntil = time + 1400;
        }
        node.scrollLeft = position;
      }
      frame = window.requestAnimationFrame(move);
    };
    frame = window.requestAnimationFrame(move);
    return () => window.cancelAnimationFrame(frame);
  }, [snap.following, snap.paused, snap.ticker[0]?.id]);
  if (!snap.following) return null;
  return <section className={`fl-ticker${snap.paused ? ' is-paused' : ''}`} aria-label="Live team events">
    <span className="fl-ticker-live">{snap.paused ? 'PAUSED' : 'LIVE'}</span>
    <div className="fl-ticker-viewport" ref={viewport} tabIndex={0} role="region" aria-label="Recent team events. Hover or focus to hold scrolling; use arrow keys to scroll.">
    <ol className="fl-ticker-list">
      {snap.ticker.length === 0 && <li className="fl-ticker-empty">Waiting for the next event…</li>}
      {snap.ticker.slice(0, 12).map(item => <li key={item.id}>
        <time>{clock(item.time)}</time>
        {item.actor && <strong>{followName(item.actor, agents)}</strong>} {item.text}
        {item.task && titles.has(item.task) && <> · <button type="button" className="fl-link" onClick={() => onOpenTask(item.task!)}>{titles.get(item.task)}</button></>}
      </li>)}
    </ol>
    </div>
    <button type="button" className="fl-ticker-pause" aria-label={snap.paused ? 'Resume team feed' : 'Pause team feed'}
      title={snap.paused ? 'Resume team feed' : 'Pause team feed'} onClick={() => setPaused(!snap.paused)}>{snap.paused ? '▶' : 'Ⅱ'}</button>
  </section>;
}

/** Normal-flow geometry lets the card grow with the shared bubble without covering task controls. */
function CardSpeech({ name, color, text }: { name: string; color: string; text: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = host.current; if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    setWidth(el.clientWidth); observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return <div ref={host} className="fl-card-speech" onClick={e => e.stopPropagation()}>
    {width > 0 && <SpeechBubble name={name} color={color} text={text} width={width} maxLines={5} />}
  </div>;
}

/** The commentary bubble, tool chip and move trail drawn on a card. */
export function CardFollowLayer({ follow, agents, part }: { follow: CardFollow; agents: Agents; part: 'bubble' | 'body' }) {
  const { live, move } = follow;
  if (part === 'bubble') {
    if (!live?.text) return null;
    return <CardSpeech name={followName(live.agent, agents)} color={agentColor(live.agent, agents)} text={live.text} />;
  }
  return <>
    {live?.tool && <p className="fl-tool" title="The tool the provider reported; providers do not report its arguments here.">
      <span>{live.tool.label || 'Using a tool'}</span> · {live.tool.status || 'started'}
    </p>}
    {move && <p className="fl-moved">
      <span aria-hidden="true">→ </span>
      {move.by ? `${followName(move.by, agents)} moved it` : 'Moved'} from {STATUS_LABELS[move.from]} · just now
    </p>}
  </>;
}

const INTERACTIVE = 'input, textarea, select, button, a[href], summary, [contenteditable=""], [contenteditable="true"], [role="button"], [role="menuitem"], [role="tab"], [role="option"]';
const OPEN_DIALOG = 'dialog[open], [role="dialog"], [role="alertdialog"]';

/** Space pauses or resumes and Esc stops following, only from outside fields, controls and dialogs.
 * Esc is left alone when something else used it first (closing a task panel or a menu). */
export function useFollowKeys(following: boolean, paused: boolean, onScreen: () => boolean) {
  const state = useRef({ following, paused, onScreen });
  state.current = { following, paused, onScreen };
  useEffect(() => {
    if (!following) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || !state.current.onScreen()) return;
      if (e.key !== ' ' && e.key !== 'Escape') return;
      const target = e.target as HTMLElement | null;
      if (target?.closest?.(INTERACTIVE) || target?.isContentEditable) return;
      if (document.querySelector(OPEN_DIALOG)) return;
      if (e.key === ' ') {
        e.preventDefault();  // no page scroll
        setPaused(!state.current.paused);
        return;
      }
      // Board's own Esc (close the task panel) marks the event; check after it has run.
      window.setTimeout(() => {
        if (e.defaultPrevented || !state.current.following) return;
        const fromBanner = !!(e.target as HTMLElement | null)?.closest?.('.fl-banner');
        setFollowing(false);
        // The banner unmounts; hand focus back to the toggle that started Follow.
        if (fromBanner) document.querySelector<HTMLElement>('.fl-toggle')?.focus();
      }, 0);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [following]);
}

/** Drop live entries whose run the server no longer reports (for example after a reconnect). */
export function useReconcileRuns(runs: RunInfo[]) {
  useEffect(() => { reconcileRuns(runs); }, [runs]);
}
