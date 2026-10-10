/* Follow mode UI: the header toggle, the Board's banner and LIVE ticker, the per-card
 * overlay, and the Space/Esc shortcuts. A live agent is pinned to a card only when it is
 * executing exactly one task; otherwise it stays on its chip in the banner, and the Board
 * never scrolls to a guessed task. */
import { useEffect, useMemo, useRef } from 'react';
import { sessionTitle } from '../lib/room';
import type { RunInfo, Task } from '../lib/types';
import { displayName, type Agents } from '../board/shared';
import { STATUS_LABELS } from '../board/shared';
import { reconcileRuns, setFollowing, setPaused, useFollow, type AgentLive, type FollowSnapshot, type TaskMove } from './followStore';
import './follow.css';

/** What follow mode adds to one task card. */
export interface CardFollow {
  live?: AgentLive;
  move?: TaskMove;
}

export interface FollowPlacement {
  cards: Map<string, CardFollow>;
  /** Live agents not pinned to a card: no executing task, or more than one. */
  unplaced: AgentLive[];
}

export function placeFollow(snap: FollowSnapshot, tasks: Task[]): FollowPlacement {
  const cards = new Map<string, CardFollow>();
  const unplaced: AgentLive[] = [];
  if (!snap.following) return { cards, unplaced };
  for (const entry of Object.values(snap.live)) {
    const executing = tasks.filter(t => t.owner === entry.agent && t.execution_state === 'executing');
    if (executing.length === 1) cards.set(executing[0].id, { live: entry });
    else unplaced.push(entry);
  }
  for (const [id, move] of Object.entries(snap.moves)) cards.set(id, { ...cards.get(id), move });
  return { cards, unplaced };
}

function agentColor(name: string, agents: Agents): string {
  return agents[name]?.color || 'var(--text-muted)';
}

function Avatar({ name, agents }: { name: string; agents: Agents }) {
  const label = displayName(name, agents);
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
  if (entry.tool && entry.tool.time >= entry.updated - 1) return `is using ${entry.tool.name}`;
  if (entry.text) return `is writing in ${sessionTitle(entry.channel)}`;
  return `started a turn in ${sessionTitle(entry.channel)}`;
}

/** Board banner while following: who is live, the commentary of agents not pinned to a card, Pause and Stop. */
export function FollowBanner({ unplaced, agents }: { unplaced: AgentLive[]; agents: Agents }) {
  const snap = useFollow();
  if (!snap.following) return null;
  const all = Object.values(snap.live);
  return <section className={`fl-banner${snap.paused ? ' is-paused' : ''}`} aria-label="Following the team">
    <div className="fl-banner-row">
      <p className="fl-banner-who" aria-live="polite">
        {snap.paused && <strong className="fl-paused">Paused · </strong>}
        {all.length === 0 ? 'Nobody is running a turn right now. Cards light up as agents start work.'
          : all.map((entry, i) => <span key={entry.agent} className="fl-who">
            {i > 0 && ' · '}
            <Avatar name={entry.agent} agents={agents} />
            <strong>{displayName(entry.agent, agents)}</strong> {activity(entry)}
          </span>)}
      </p>
      <span className="fl-banner-hint">Shortcuts work outside text fields</span>
      <button type="button" className="fl-btn" onClick={() => setPaused(!snap.paused)}>{snap.paused ? 'Resume' : 'Pause'} · Space</button>
      <button type="button" className="fl-btn" onClick={() => setFollowing(false)}>Stop · Esc</button>
    </div>
    {unplaced.filter(e => e.text).map(entry => <p key={entry.agent} className="fl-banner-bubble" style={{ '--agent-color': agentColor(entry.agent, agents) } as React.CSSProperties}>
      <strong>{displayName(entry.agent, agents)}</strong> {entry.text}
      <small> · not tied to one task</small>
    </p>)}
  </section>;
}

function clock(time: number): string {
  return new Date(time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Bottom strip of recent events, newest first. Task names open the task. */
export function FollowTicker({ tasks, agents, onOpenTask }: { tasks: Task[]; agents: Agents; onOpenTask: (id: string) => void }) {
  const snap = useFollow();
  const titles = useMemo(() => new Map(tasks.map(t => [t.id, t.title])), [tasks]);
  if (!snap.following) return null;
  return <section className="fl-ticker" aria-label="Live events">
    <span className="fl-ticker-live">{snap.paused ? 'PAUSED' : 'LIVE'}</span>
    <ol className="fl-ticker-list">
      {snap.ticker.length === 0 && <li className="fl-ticker-empty">Waiting for the next event…</li>}
      {snap.ticker.slice(0, 12).map(item => <li key={item.id}>
        <time>{clock(item.time)}</time>
        {item.actor && <strong>{displayName(item.actor, agents)}</strong>} {item.text}
        {item.task && titles.has(item.task) && <> · <button type="button" className="fl-link" onClick={() => onOpenTask(item.task!)}>{titles.get(item.task)}</button></>}
      </li>)}
    </ol>
  </section>;
}

/** The commentary bubble, tool chip and move trail drawn on a card. */
export function CardFollowLayer({ follow, agents, part }: { follow: CardFollow; agents: Agents; part: 'bubble' | 'body' }) {
  const { live, move } = follow;
  if (part === 'bubble') {
    if (!live?.text) return null;
    return <p className="fl-bubble" style={{ '--agent-color': agentColor(live.agent, agents) } as React.CSSProperties}>
      <span><strong>{displayName(live.agent, agents)}</strong> {live.text}</span>
    </p>;
  }
  return <>
    {live?.tool && <p className="fl-tool" title="The tool the provider reported; providers do not report its arguments here.">
      <code>{live.tool.name}</code> · {live.tool.status || 'started'}
    </p>}
    {move && <p className="fl-moved">
      <span aria-hidden="true">→ </span>
      {move.by ? `${displayName(move.by, agents)} moved it` : 'Moved'} from {STATUS_LABELS[move.from]} · just now
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
      window.setTimeout(() => { if (!e.defaultPrevented && state.current.following) setFollowing(false); }, 0);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [following]);
}

/** Drop live entries whose run the server no longer reports (for example after a reconnect). */
export function useReconcileRuns(runs: RunInfo[]) {
  useEffect(() => { reconcileRuns(runs); }, [runs]);
}
