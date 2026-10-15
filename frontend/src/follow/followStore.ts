/* Follow mode: a live view of what the team is doing, built only from events the room
 * already emits (runtime_start/delta/tool/end, project_state, browser_activity).
 * Tool events carry a name and status, not reliable parameters (Claude reports input {} at
 * block start; Codex reports a generic mcpToolCall), so nothing here invents details.
 * Events are tracked whenever the shell is open, so turning follow on shows the current state
 * at once. Pause freezes what is shown; resuming catches up to the latest state. */
import { useSyncExternalStore } from 'react';
import type { ProjectState, RoomEvent, Review, Task, TaskStatus } from '../lib/types';
import { isVideoAttachment } from '../lib/mediaAttachment';
import { readStored, writeStored } from '../lib/storage';
import { STORAGE_KEYS } from '../lib/constants';

export interface LiveTool {
  name: string;
  label?: string;
  status: string;
  time: number;
}

/** One agent's running turn. */
export interface AgentLive {
  agent: string;
  run: string;
  channel: string;
  /** Explicit association supplied with this run; never inferred from a room reference. */
  task?: string | null;
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

/** The finished artifact the picture-in-picture shows. Markup is atomic: this is the completed
 * annotation, never a live drawing. A browser item shows the shared preview as it is now. */
export interface PipItem {
  id: number;
  kind: 'markup' | 'image' | 'video' | 'browser';
  actor: string;
  time: number;
  /** Upload url for markup and images; the preview page address for the browser. */
  url: string;
  /** The markup note: one line per numbered mark. */
  note?: string;
  source?: string;
  page?: string;
  task?: string;
  /** Browser: the project the frame belongs to, and what the agent did. */
  project?: string;
  action?: string;
}

export interface FollowSnapshot {
  following: boolean;
  paused: boolean;
  live: Record<string, AgentLive>;
  ticker: TickerItem[];
  moves: Record<string, TaskMove>;
  pip: PipItem | null;
  pips: PipItem[];
}

const TEXT_TAIL = 50000; // runaway guard; the bubble owns visual collapse, not source truncation
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
let pip: PipItem | null = null;
let pipSerial = 0;
const pips: PipItem[] = [];
let currentProject = "";
// Upload urls already shown, so a markup posted to chat and attached to a task appears once.
const pipSeen = new Set<string>();

let shown: FollowSnapshot = snapshot();
const listeners = new Set<() => void>();
let attached = false;

/** Copies of the tracked entries: onDelta and onTool mutate them, and a paused view must not change. */
function snapshot(): FollowSnapshot {
  return {
    following,
    paused,
    live: Object.fromEntries([...live].map(([agent, entry]) => [agent, { ...entry, tool: entry.tool && { ...entry.tool } }])),
    ticker: ticker.slice(),
    moves: Object.fromEntries(moves),
    pip: pip && { ...pip },
    pips: pips.map(item => ({...item})),
  };
}

function showPip(item: Omit<PipItem, 'id'>) {
  pip = { project: currentProject, ...item, id: ++pipSerial };
  pips.unshift(pip);
  pips.length = Math.min(pips.length, 12);
}

/** Close the picture-in-picture until the next artifact arrives. While paused, only the card goes:
 * the rest of the frozen view (live entries, ticker, moves) must not advance. */
export function closePip(id?: number) {
  if (id !== undefined) {
    const index = pips.findIndex(item => item.id === id); if (index >= 0) pips.splice(index, 1);
    if (pip?.id === id) pip = pips[0] || null;
    if (paused) { shown = {...shown, pips: shown.pips.filter(item => item.id !== id), pip: shown.pip?.id === id ? null : shown.pip}; listeners.forEach(listener => listener()); }
    else publish(true);
    return;
  }
  pips.length = 0;
  if (paused) {
    // A newer artifact that arrived during the pause still shows on resume.
    if (pip?.id === shown.pip?.id) pip = null;
    shown = { ...shown, pip: null, pips: [] };
    listeners.forEach(listener => listener());
    return;
  }
  pip = null;
  publish(true);
}

const IMAGE_URL = /^\/uploads\/[\w.-]+\.(png|jpe?g|gif|webp|webm|mp4)$/i;

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
  label?: string;
  task_id?: string | null;
  status?: string;
  outcome?: string;
  sealed?: boolean;
}

// Raw streamed text per run and message, so the bubble shows the tail of the current message.
const streams = new Map<string, { message: string; text: string; draft: boolean }>();
// A delayed HTTP snapshot must not undo focus already received on the event stream.
const focusEvents = new Set<string>();

function applyFocus(entry: AgentLive, data: { task_id?: string | null }) {
  if ('task_id' in data) entry.task = data.task_id;
}

function eventEntry(d: RuntimeData) {
  const previous = live.get(d.agent);
  if (previous && previous.run !== d.run) focusEvents.delete(previous.run);
  const entry = ensure(d.agent, d.run, d.channel ?? '');
  applyFocus(entry, d);
  if ('task_id' in d) focusEvents.add(d.run);
  return entry;
}

function onFocus(event: RoomEvent) {
  const d = event.data as RuntimeData;
  if (ended.has(d.run)) return;
  eventEntry(d).updated = now();
  publish();
}

function onStart(event: RoomEvent) {
  const d = event.data as RuntimeData;
  eventEntry(d);
  tick(d.agent, 'started a turn', undefined);
  publish();
}

function onDelta(event: RoomEvent) {
  const d = event.data as RuntimeData;
  const entry = eventEntry(d);
  const draft = event.type === 'runtime_draft';
  let stream = streams.get(d.run);
  // The first public delta repeats the full message after a draft, rather than continuing its chunks.
  if (!stream || stream.message !== (d.message ?? '') || stream.draft && !draft) {
    stream = { message: d.message ?? '', text: '', draft };
    streams.set(d.run, stream);
  }
  stream.draft = draft;
  // Retain the current reply for expanded reading; cap only runaway streams.
  stream.text = d.sealed ? '' : (stream.text + (d.text ?? '')).slice(-TEXT_TAIL);
  entry.text = d.sealed ? 'Sealed position' : tail(stream.text);
  entry.updated = now();
  publish();
}

function onTool(event: RoomEvent) {
  const d = event.data as RuntimeData;
  const entry = eventEntry(d);
  entry.tool = { name: d.tool ?? 'tool', label: d.label, status: d.status ?? '', time: now() };
  entry.updated = now();
  publish();
}

// Runs that ended here, so a snapshot that lags their runtime_end never seeds them again.
const ended = new Set<string>();

function onEnd(event: RoomEvent) {
  const d = event.data as RuntimeData;
  ended.add(d.run);
  if (ended.size > 200) ended.delete(ended.values().next().value as string);
  const entry = live.get(d.agent);
  if (entry && entry.run === d.run) live.delete(d.agent);
  streams.delete(d.run);
  focusEvents.delete(d.run);
  tick(d.agent, d.outcome && d.outcome !== 'completed' ? `turn ended (${d.outcome})` : 'finished a turn');
  publish();
}

function onProjectState(event: RoomEvent) {
  const state = event.data as ProjectState & {project_id?: string};
  if (!state || !Array.isArray(state.tasks)) return;
  if (state.project_id && currentProject && state.project_id !== currentProject) {
    lastTasks = null; lastReviews = null; moves.clear(); ticker.length = 0; pips.length = 0; pip = null; pipSeen.clear();
  }
  currentProject = state.project_id || currentProject;
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
      // A new image on a task (room_markup with task_id, or an attachment update).
      const had = new Set((before.attachments ?? []).map(a => a.url));
      const added = (task.attachments ?? []).filter(a => !had.has(a.url) && IMAGE_URL.test(a.url) && !pipSeen.has(a.url));
      const last = added[added.length - 1];
      if (last) {
        pipSeen.add(last.url);
        showPip({ kind: isVideoAttachment(last.url) ? 'video' : last.source ? 'markup' : 'image', actor: by, time, url: last.url, note: last.note, source: last.source, task: id });
        tick(by, isVideoAttachment(last.url) ? 'attached a video to' : last.source ? 'annotated an image on' : 'attached an image to', id, time);
      }
    }
  }
  if (lastReviews) {
    for (const [id, review] of reviews) {
      const before = lastReviews.get(id);
      // Only a task already on the last board counts: after a project switch, that project's
      // existing pending reviews are not new.
      if (!before && review.status === 'pending' && lastTasks?.has(review.task_id)) {
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
  project?: string;
  page?: string;
  active_page?: string;
  running?: boolean;
  pages?: {id: string; url: string}[];
  activity?: { actor?: string; label?: string; action?: string; phase?: string; time?: number; page?: string } | null;
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
  // Agent actions report actor = run id and label = agent name (room_runtime); the user's are actor 'user'.
  const who = a.actor === 'user' ? 'user' : a.label || a.actor || '';
  // Phases are working, failed and done: only a finished action is told as done, a failure as a failure.
  if (!words || !who || (a.phase !== 'done' && a.phase !== 'failed')) return;
  if (a.phase === 'failed') {
    tick(who, `browser action failed: ${a.action}`, undefined, a.time);
    publish();
    return;
  }
  const where = a.action === 'open' && d.url ? ` · ${d.url.replace(/^https?:\/\/(127\.0\.0\.1|localhost)/, '')}` : '';
  tick(who, words + where, undefined, a.time);
  // An agent acting in the shared preview: show the page as it is now. The user's own actions are not echoed.
  if (who !== 'user' && a.action !== 'release' && d.project) {
    const page = a.page || d.page || d.active_page;
    const url = d.pages?.find(p => p.id === page)?.url || (!a.page || a.page === d.page ? d.url : '') || '';
    // A claim on a stopped browser has no page or frame to show.
    if (page && d.running !== false) showPip({ kind: 'browser', actor: who, time: a.time, url, project: d.project, page, action: words });
  }
  publish();
}

interface MessageData {
  sender?: string;
  channel?: string;
  attachments?: { url: string; name?: string; note?: string; source?: string }[];
}

/** A live chat message with an image from an agent: room_markup posts its finished annotation this way. */
function onMessage(event: RoomEvent) {
  const m = event.data as MessageData;
  if (!m?.sender || m.sender === 'user' || m.sender === 'system') return;
  if (!knownAgents.has(m.sender) && !live.has(m.sender)) return;  // only agents, not people or system notices
  const images = (m.attachments ?? []).filter(a => IMAGE_URL.test(a.url) && !pipSeen.has(a.url));
  const last = images[images.length - 1];
  if (!last) return;
  pipSeen.add(last.url);
  const time = now();
  showPip({ kind: isVideoAttachment(last.url) ? 'video' : last.source ? 'markup' : 'image', actor: m.sender, time, url: last.url, note: last.note, source: last.source });
  tick(m.sender, isVideoAttachment(last.url) ? 'shared a video' : last.source ? 'annotated an image' : 'shared an image', undefined, time);
  publish();
}

// Agent ids from runtime_state, which the room broadcasts on every status change.
const knownAgents = new Set<string>();
function onRuntimeState(event: RoomEvent) {
  const data = event.data as { agents?: Record<string, unknown>; project_id?: string; runs?: RunSnapshot[] } | undefined;
  if (data?.project_id && data.project_id !== currentProject) {
    currentProject = data.project_id; lastTasks = null; lastReviews = null; lastBrowserTime = 0;
    live.clear(); streams.clear(); ended.clear(); moves.clear(); ticker.length = 0; pips.length = 0; pip = null; pipSeen.clear();
    focusEvents.clear();
    paused = false; publish(true);
  }
  const agents = data?.agents;
  if (agents) {
    knownAgents.clear();
    Object.keys(agents).forEach(name => knownAgents.add(name));
  }
  if (data?.runs) reconcileRuns(data.runs, true);
}

const HANDLERS: [string, Listener][] = [
  ['runtime_state', onRuntimeState],
  ['runtime_start', onStart],
  ['runtime_focus', onFocus],
  ['settings', () => focusEvents.clear()],
  ['runtime_delta', onDelta],
  ['runtime_draft', onDelta],
  ['runtime_tool', onTool],
  ['runtime_end', onEnd],
  ['project_state', onProjectState],
  ['browser_activity', onBrowser],
  ['message', onMessage],
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
type RunSnapshot = { id: string; agent: string; channel?: string; task_id?: string | null };
export function reconcileRuns(running: RunSnapshot[], pushed = false) {
  const ids = new Set(running.map(r => r.id));
  let changed = false;
  // Turns already running when the page loads or reconnects have sent their runtime_start before we
  // listened: seed them from the snapshot so the live stage shows them from the start. A snapshot can
  // lag the events (a run that ended while disconnected), so it fills only an agent with no live entry,
  // or replaces one that has gone quiet; a run with fresh events is never overwritten by it.
  const quietSince = now() - RECONCILE_QUIET_SECONDS;
  for (const r of running) {
    const current = live.get(r.agent);
    if (ended.has(r.id)) continue;
    if (current?.run === r.id) {
      if ('task_id' in r && (pushed || !focusEvents.has(r.id))) {
        changed ||= current.task !== r.task_id;
        applyFocus(current, r);
        if (pushed) focusEvents.add(r.id);
      }
      continue;
    }
    if (current && current.updated >= quietSince) continue;
    if (current) {
      live.delete(r.agent);
      streams.delete(current.run);
      focusEvents.delete(current.run);
    }
    applyFocus(ensure(r.agent, r.id, r.channel ?? ''), r);
    if (pushed && 'task_id' in r) focusEvents.add(r.id);
    changed = true;
  }
  // The same lag the other way: a snapshot fetched just before a turn started; only drop quiet entries.
  for (const [agent, entry] of live) {
    if (!ids.has(entry.run) && entry.updated < quietSince) {
      live.delete(agent);
      streams.delete(entry.run);
      focusEvents.delete(entry.run);
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
