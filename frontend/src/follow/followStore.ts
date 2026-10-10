/* Follow mode: a live view of what the team is doing, built only from events the room
 * already emits (runtime_start/delta/tool/end, project_state, browser_activity).
 * Tool events carry a name and status, not reliable parameters (Claude reports input {} at
 * block start; Codex reports a generic mcpToolCall), so nothing here invents details.
 * Events are tracked whenever the shell is open, so turning follow on shows the current state
 * at once. Pause freezes what is shown; resuming catches up to the latest state. */
import { useSyncExternalStore } from 'react';
import type { ProjectState, RoomEvent, Review, Task, TaskStatus } from '../lib/types';
import { readStored, writeStored } from '../lib/storage';
import { STORAGE_KEYS } from '../lib/constants';

export interface LiveTool {
  name: string;
  status: string;
  time: number;
}

/** One agent's running turn. */
export interface AgentLive {
  agent: string;
  run: string;
  channel: string;
  /** Tail of the agent's latest streamed message; kept after the message ends until the next one. */
  text: string;
  /** Last tool the agent's provider reported in this turn. */
  tool?: LiveTool;
  started: number;
  updated: number;
}

export interface TickerItem {
  id: number;
  time: number;
  /** Who did it: an agent id, 'user', or '' when the event does not say. */
  actor: string;
  text: string;
  task?: string;
}

/** A task that changed column while follow was able to see it. */
export interface TaskMove {
  from: TaskStatus;
  to: TaskStatus;
  by: string;
  time: number;
}

export interface FollowSnapshot {
  following: boolean;
  paused: boolean;
  live: Record<string, AgentLive>;
  ticker: TickerItem[];
  moves: Record<string, TaskMove>;
}

const TEXT_TAIL = 180;
const TICKER_KEPT = 30;
const MOVE_SHOWN_MS = 6000;
const RECONCILE_QUIET_SECONDS = 10;
const PUBLISH_MS = 200;
const STATUS_WORDS: Record<TaskStatus, string> = { open: 'Open', working: 'Working', blocked: 'Blocked', review: 'In review', done: 'Done' };

type Listener = (event: RoomEvent) => void;

const now = () => Date.now() / 1000;

let following = readStored(STORAGE_KEYS.follow) === '1';
let paused = false;
const live = new Map<string, AgentLive>();
const ticker: TickerItem[] = [];
const moves = new Map<string, TaskMove>();
let tickerSerial = 0;
let lastTasks: Map<string, Task> | null = null;
let lastReviews: Map<string, Review> | null = null;
let lastBrowserTime = 0;

let shown: FollowSnapshot = snapshot();
const listeners = new Set<() => void>();
let attached = false;

function snapshot(): FollowSnapshot {
  return {
    following,
    paused,
    live: Object.fromEntries(live),
    ticker: ticker.slice(),
    moves: Object.fromEntries(moves),
  };
}

let publishTimer: number | null = null;

function flush() {
  if (publishTimer !== null) window.clearTimeout(publishTimer);
  publishTimer = null;
  shown = snapshot();
  listeners.forEach(listener => listener());
}

/** Publish the tracked state, unless follow is off or paused (the shown view stays frozen).
 * Streamed text arrives in many small deltas, so updates are coalesced to a few per second. */
function publish(force = false) {
  if (force) return flush();
  if (!following || paused || publishTimer !== null) return;
  publishTimer = window.setTimeout(flush, PUBLISH_MS);
}

function tick(actor: string, text: string, task?: string, time = now()) {
  ticker.unshift({ id: ++tickerSerial, time, actor, text, task });
  ticker.length = Math.min(ticker.length, TICKER_KEPT);
}

function tail(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > TEXT_TAIL ? '…' + flat.slice(flat.length - TEXT_TAIL + 1) : flat;
}

function ensure(agent: string, run: string, channel: string): AgentLive {
  let entry = live.get(agent);
  if (!entry || entry.run !== run) {
    entry = { agent, run, channel, text: '', started: now(), updated: now() };
    live.set(agent, entry);
  }
  return entry;
}

interface RuntimeData {
  run: string;
  agent: string;
  channel?: string;
  text?: string;
  message?: string;
  tool?: string;
  status?: string;
  outcome?: string;
}

// Raw streamed text per run and message, so the bubble shows the tail of the current message.
const streams = new Map<string, { message: string; text: string }>();

function onStart(event: RoomEvent) {
  const d = event.data as RuntimeData;
  ensure(d.agent, d.run, d.channel ?? '');
  tick(d.agent, 'started a turn', undefined);
  publish();
}

function onDelta(event: RoomEvent) {
  const d = event.data as RuntimeData;
  const entry = ensure(d.agent, d.run, d.channel ?? '');
  let stream = streams.get(d.run);
  if (!stream || stream.message !== d.message) {
    stream = { message: d.message ?? '', text: '' };
    streams.set(d.run, stream);
  }
  // Keep a bounded window; only the tail is ever shown.
  stream.text = (stream.text + (d.text ?? '')).slice(-TEXT_TAIL * 3);
  entry.text = tail(stream.text);
  entry.updated = now();
  publish();
}

function onTool(event: RoomEvent) {
  const d = event.data as RuntimeData;
  const entry = ensure(d.agent, d.run, d.channel ?? '');
  entry.tool = { name: d.tool ?? 'tool', status: d.status ?? '', time: now() };
  entry.updated = now();
  publish();
}

function onEnd(event: RoomEvent) {
  const d = event.data as RuntimeData;
  const entry = live.get(d.agent);
  if (entry && entry.run === d.run) live.delete(d.agent);
  streams.delete(d.run);
  tick(d.agent, d.outcome && d.outcome !== 'completed' ? `turn ended (${d.outcome})` : 'finished a turn');
  publish();
}

function onProjectState(event: RoomEvent) {
  const state = event.data as ProjectState;
  if (!state || !Array.isArray(state.tasks)) return;
  const tasks = new Map(state.tasks.map(t => [t.id, t]));
  const reviews = new Map((state.reviews ?? []).map(r => [r.id, r]));
  const time = now();
  // The first frame (and a switch to another project, which shares no task ids) is a baseline.
  if (lastTasks) {
    for (const [id, task] of tasks) {
      const before = lastTasks.get(id);
      if (!before) continue;
      const latest = task.updates[task.updates.length - 1];
      const by = latest?.by ?? task.owner;
      if (before.status !== task.status) {
        moves.set(id, { from: before.status, to: task.status, by, time });
        tick(by, `moved to ${STATUS_WORDS[task.status]}`, id, time);
        window.setTimeout(() => {
          if (moves.get(id)?.time === time) {
            moves.delete(id);
            publish();
          }
        }, MOVE_SHOWN_MS);
      } else if (before.owner !== task.owner && task.owner) {
        tick(task.owner, 'took', id, time);
      }
    }
  }
  if (lastReviews) {
    for (const [id, review] of reviews) {
      const before = lastReviews.get(id);
      if (!before && review.status === 'pending' && tasks.has(review.task_id)) {
        tick(review.requested_by, `asked ${review.reviewer} to review`, review.task_id, time);
      } else if (before && before.status !== review.status && (review.status === 'approved' || review.status === 'changes_requested')) {
        tick(review.reviewer, review.status === 'approved' ? 'approved' : 'requested changes on', review.task_id, time);
      }
    }
  }
  lastTasks = tasks;
  lastReviews = reviews;
  publish();
}

interface BrowserData {
  url?: string;
  activity?: { actor?: string; label?: string; action?: string; phase?: string; time?: number } | null;
}

// Actions worth a ticker line; status checks and inspection are background noise.
const BROWSER_WORDS: Record<string, string> = {
  claim: 'took the browser',
  release: 'released the browser',
  open: 'opened a preview',
  click: 'clicked in the browser',
  type: 'typed in the browser',
  screenshot: 'took a browser screenshot',
  reload: 'reloaded the preview',
};

function onBrowser(event: RoomEvent) {
  const d = event.data as BrowserData;
  const a = d?.activity;
  if (!a?.action || !a.time || a.time <= lastBrowserTime) return;
  lastBrowserTime = a.time;
  const words = BROWSER_WORDS[a.action];
  if (!words || a.phase === 'started') return;
  const where = a.action === 'open' && d.url ? ` · ${d.url.replace(/^https?:\/\/(127\.0\.0\.1|localhost)/, '')}` : '';
  tick(a.actor ?? '', words + where, undefined, a.time);
  publish();
}

const HANDLERS: [string, Listener][] = [
  ['runtime_start', onStart],
  ['runtime_delta', onDelta],
  ['runtime_tool', onTool],
  ['runtime_end', onEnd],
  ['project_state', onProjectState],
  ['browser_activity', onBrowser],
];

function attach() {
  if (attached) return;
  const run = () => {
    if (!window.Hub || attached) return;
    attached = true;
    HANDLERS.forEach(([type, fn]) => window.Hub!.on(type, fn));
  };
  if (window.Hub) run();
  else window.addEventListener('load', run, { once: true });
}

/** Drop runs the server no longer reports (a missed runtime_end after a reconnect). */
export function reconcileRuns(running: { id: string; agent: string }[]) {
  const ids = new Set(running.map(r => r.id));
  let changed = false;
  // A snapshot fetched just before a turn started can lag its events; only drop quiet entries.
  const quietSince = now() - RECONCILE_QUIET_SECONDS;
  for (const [agent, entry] of live) {
    if (!ids.has(entry.run) && entry.updated < quietSince) {
      live.delete(agent);
      streams.delete(entry.run);
      changed = true;
    }
  }
  if (changed) publish();
}

export function setFollowing(on: boolean) {
  following = on;
  paused = false;
  writeStored(STORAGE_KEYS.follow, on ? '1' : '0');
  publish(true);
}

export function setPaused(on: boolean) {
  paused = on;
  publish(true);
}

function subscribe(listener: () => void) {
  attach();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useFollow(): FollowSnapshot {
  return useSyncExternalStore(subscribe, () => shown);
}

// Start tracking as soon as the shell loads, so follow has state to show when turned on.
attach();
