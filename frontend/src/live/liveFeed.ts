/* Live view data seam (task b42437b9). Live is about the team right now: who is working, with whom,
 * on what, and what they last produced. The shape follows backend's pinned /api/live contract
 * (b1af7a3a, follow-up ec632edd). While that route is not installed (404), the same model is derived
 * from events the room already sends, so switching to the server feed changes nothing downstream.
 * Speech (streamed reply text) and colours are not part of the feed contract; they always come from
 * runtime_delta and runtime_state. Nothing here invents progress, tool parameters or task links:
 * a task is the run's explicit task, else the agent's single executing task. */
import { useEffect, useState } from 'react';
import { api, ApiError, errorMessage, runtime } from '../lib/api';
import type { ProjectState, RoomEvent, RuntimeState, Task } from '../lib/types';

export type LiveAgentState = 'working' | 'waiting_on_user' | 'failed' | 'stopped' | 'idle';
export type LiveArtifactKind = 'screenshot' | 'markup' | 'upload' | 'browser_frame';
export type LiveInteractionKind = 'mention' | 'handoff' | 'review_request' | 'review_verdict' | 'followup'
  | 'followup_result' | 'decision_turn' | 'question' | 'answer';

export interface LiveArtifact {
  url: string;
  kind: LiveArtifactKind;
  task_id?: string;
  at: number;
  /** Markup: the original image the marks were drawn on. */
  source_url?: string;
  /** Browser frame: the shared-browser page and when the frame was taken. */
  page?: string;
  frame_at?: number;
  /** Markup note, one line per numbered mark (derived mode only). */
  note?: string;
}

export interface LiveAgent {
  id: string;
  name: string;
  /** What people call them on the stage: the display label when it is short, else the @handle as words
   * ('Product Design and Accessibility' reads 'Design'). The full label stays for titles and screen readers. */
  short: string;
  order: number;
  state: LiveAgentState;
  reason?: string;
  activity: string;
  task_id?: string;
  task_title?: string;
  since: number;
  artifact: LiveArtifact | null;
  /** Not in the feed contract: runtime_state colour and the tail of the current streamed reply. */
  color: string;
  speech: string;
  speechAt: number;
}

export interface LiveInteraction {
  id: string;
  at: number;
  channel?: string;
  from: string;
  to: string;
  kind: LiveInteractionKind;
  task_id?: string;
  task_title?: string;
  record_id?: string;
  text: string;
  from_owner?: string;
  to_owner?: string;
  /** Client only: arrived while the view was open, so it may animate. Snapshot history never does. */
  fresh?: boolean;
}

/** Something waiting on you right now (runtime_state approvals): a question to answer, or a tool to allow. */
export interface LiveApproval {
  id: string;
  agent: string;
  channel: string;
  tool: string;
  question: boolean;
  /** The question itself, or why the tool needs approval. */
  text: string;
}

export interface LiveFeed {
  agents: LiveAgent[];
  interactions: LiveInteraction[];
  approvals: LiveApproval[];
  project: string;
  source: 'server' | 'derived';
  ready: boolean;
  connected: boolean;
  error: string;
}

const RING = 200;
const SPEECH_TAIL = 160;
const PUBLISH_MS = 120;
const now = () => Date.now() / 1000;
const flat = (text: string) => text.replace(/\s+/g, ' ').trim();
const clip = (text: string, n: number) => { const t = flat(text); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const tail = (text: string, n: number) => { const t = flat(text); return t.length > n ? '…' + t.slice(t.length - n + 1) : t; };
/** Pictures and short videos an agent shared (videos: rendered demos, recordings). */
const IMAGE = /^\/uploads\/[\w.-]+\.(png|jpe?g|gif|webp|webm|mp4)$/i;
export const isVideo = (url: string) => /\.(webm|mp4)(\?|$)/i.test(url);
const MENTION = /(^|[^\w@])@([a-z0-9][\w-]{0,40})/gi;

/** The words to show for an approval: the question asked, else the reason the tool needs approval. */
function askText(q: { params: unknown; reason: string; tool: string; question: boolean }): string {
  const p = (q.params && typeof q.params === 'object' ? q.params : {}) as { message?: unknown; questions?: { question?: unknown }[] };
  const asked = typeof p.message === 'string' ? p.message : Array.isArray(p.questions) ? p.questions.map(x => x?.question).find(x => typeof x === 'string') as string | undefined : undefined;
  return clip(asked || q.reason || (q.question ? 'Has a question for you' : `Wants to use ${q.tool}`), 280);
}

/** A stage name: short labels as they are (Astra, Opus 5.5); long role titles become the handle in words. */
export function shortName(label: string, handle: string): string {
  if (label.length <= 12) return label;
  const words = handle.replace(/^@/, '').split(/[-_\s]+/).filter(Boolean);
  return words.length ? words.map(w => w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)).join(' ') : label.split(/\s+/)[0];
}

const empty = (): LiveFeed => ({ agents: [], interactions: [], approvals: [], project: '', source: 'derived', ready: false, connected: false, error: '' });

interface RuntimeData { run: string; agent: string; channel?: string; text?: string; message?: string; tool?: string; label?: string;
  task_id?: string; status?: string; outcome?: string; sealed?: boolean }

/** One live feed per open Live view. Returns a stable snapshot that changes at most every PUBLISH_MS. */
export function useLiveFeed(active: boolean): LiveFeed {
  const [feed, setFeed] = useState<LiveFeed>(empty);
  useEffect(() => {
    if (!active) return;
    let stopped = false, timer: number | null = null, attached = false, generation = 0;
    let source: LiveFeed['source'] = 'derived', ready = false, error = '', project = '', conflicts = 0;
    const agents = new Map<string, LiveAgent>();
    const interactions: LiveInteraction[] = [];
    const seenInteraction = new Set<string>();
    const handles = new Map<string, string>();         // @handle or label (lower case) -> agent id
    const runs = new Map<string, string>();            // run -> agent
    const streams = new Map<string, { message: string; text: string; draft: boolean }>();
    const openQuestions = new Map<string, { agent: string; text: string }>(); // approval id -> asker
    const explicitTask = new Map<string, string>();     // agent -> task_id its running turn named
    let tasks = new Map<string, Task>();
    let approvals: LiveApproval[] = [];
    let reviews = new Map<string, NonNullable<ProjectState['reviews']>[number]>();
    let followups = new Map<string, NonNullable<ProjectState['followups']>[number]>();

    const publish = (force = false) => {
      if (stopped) return;
      const flush = () => {
        timer = null;
        if (stopped) return;
        setFeed({
          agents: [...agents.values()].sort((a, b) => a.order - b.order).map(a => ({ ...a, artifact: a.artifact && { ...a.artifact } })),
          interactions: interactions.slice(),
          approvals: approvals.slice(),
          project, source, ready, error,
          connected: window.ws?.readyState === WebSocket.OPEN,
        });
      };
      if (force) { if (timer !== null) window.clearTimeout(timer); flush(); return; }
      if (timer === null) timer = window.setTimeout(flush, PUBLISH_MS);
    };

    const agentFor = (id: string): LiveAgent | undefined => agents.get(id);
    const idFor = (nameOrHandle: string) => handles.get(nameOrHandle.toLowerCase()) || (agents.has(nameOrHandle) ? nameOrHandle : '');
    const taskTitle = (id?: string) => (id && tasks.get(id)?.title) || undefined;

    const interact = (item: LiveInteraction) => {
      if (seenInteraction.has(item.id)) return;
      seenInteraction.add(item.id);
      interactions.unshift(item);
      interactions.sort((a, b) => b.at - a.at);
      if (interactions.length > RING) for (const dropped of interactions.splice(RING)) seenInteraction.delete(dropped.id);
      publish();
    };

    /** The executing task for an agent when exactly one exists; never a guess between several. */
    const executingTask = (agent: string): Task | undefined => {
      const mine = [...tasks.values()].filter(t => t.owner === agent && t.execution_state === 'executing');
      return mine.length === 1 ? mine[0] : undefined;
    };
    const settleTask = (a: LiveAgent, explicit?: string) => {
      if (source === 'server') return;
      if (explicit) explicitTask.set(a.id, explicit);
      const id = explicitTask.get(a.id) || executingTask(a.id)?.id;
      a.task_id = id || undefined;
      a.task_title = taskTitle(id);
    };
    const setState = (a: LiveAgent, state: LiveAgentState, reason?: string) => {
      if (a.state !== state) a.since = now();
      a.state = state;
      a.reason = reason;
    };

    // ---------- runtime_state: roster, colours, stopped/waiting (both modes need names and colours) ----------
    function applyRoom(next: RuntimeState) {
      if (!next?.agents) return;
      if (project && next.project_id && next.project_id !== project) { reset(); }
      project = next.project_id || project;
      // What is waiting on you comes straight from the room's live approvals, in both feed modes.
      approvals = (next.approvals ?? []).map(q => ({ id: q.id, agent: q.agent, channel: q.channel, tool: q.tool, question: !!q.question, text: askText(q) }));
      const ids = Object.keys(next.agents).filter(id => !next.agents[id].archived);
      handles.clear();
      ids.forEach((id, order) => {
        const s = next.agents[id];
        handles.set((s.handle || id).toLowerCase(), id);
        handles.set(s.label.toLowerCase(), id);
        handles.set(id.toLowerCase(), id);
        const a = agents.get(id) ?? { id, name: s.label, short: shortName(s.label, s.handle || id), order, state: 'idle' as LiveAgentState, activity: '', since: now(), artifact: null, color: s.color, speech: '', speechAt: 0 };
        a.name = s.label; a.short = shortName(s.label, s.handle || id); a.color = s.color || 'var(--accent)';
        if (source === 'derived') a.order = order;
        agents.set(id, a);
      });
      for (const id of [...agents.keys()]) if (!ids.includes(id)) agents.delete(id);
      for (const r of next.runs ?? []) runs.set(r.id, r.agent);
      if (source === 'derived') {
        const running = new Set((next.runs ?? []).map(r => r.agent));
        const waiting = new Map((next.approvals ?? []).map(q => [q.agent, q]));
        for (const a of agents.values()) {
          const s = next.agents[a.id];
          const ask = waiting.get(a.id);
          if (ask) { setState(a, 'waiting_on_user'); a.activity = ask.question ? 'waiting for your answer' : 'waiting for your approval'; }
          else if (s.paused) { setState(a, 'stopped'); a.activity = 'stopped'; }
          else if (s.state === 'blocked' || s.state === 'login_required') { setState(a, 'failed', s.detail || (s.state === 'login_required' ? 'Sign-in needed' : 'Unavailable')); a.activity = 'unavailable'; }
          else if (running.has(a.id)) { if (a.state !== 'working') { const was = a.state; setState(a, 'working'); if (was === 'waiting_on_user' || !a.activity) a.activity = 'working'; } }
          else if (a.state === 'working' || a.state === 'waiting_on_user' || a.state === 'stopped') { setState(a, 'idle'); a.activity = ''; }
          settleTask(a);
        }
        // Questions to Zero: an approval marked as a question rises to the You area; its disappearance is the answer.
        const current = new Set((next.approvals ?? []).map(q => q.id));
        for (const q of next.approvals ?? []) if (q.question && !openQuestions.has(q.id)) {
          openQuestions.set(q.id, { agent: q.agent, text: q.reason });
          interact({ id: `question:${q.id}`, at: now(), channel: q.channel, from: q.agent, to: 'user', kind: 'question', record_id: q.id, text: clip(q.reason || 'Has a question for you', 140), fresh: ready });
        }
        for (const [id, q] of [...openQuestions]) if (!current.has(id)) {
          openQuestions.delete(id);
          interact({ id: `answer:${id}`, at: now(), from: 'user', to: q.agent, kind: 'answer', record_id: id, text: 'Answered', fresh: ready });
        }
      }
      publish();
    }

    // ---------- project_state: tasks, handoffs, reviews, follow-ups, attachments (derived mode) ----------
    function applyProject(next: ProjectState, baseline: boolean) {
      if (!Array.isArray(next?.tasks)) return;
      const before = tasks, beforeReviews = reviews, beforeFollowups = followups;
      tasks = new Map(next.tasks.map(t => [t.id, t]));
      reviews = new Map((next.reviews ?? []).map(r => [r.id, r]));
      followups = new Map((next.followups ?? []).map(f => [f.id, f]));
      if (source === 'server') { publish(); return; }
      const fresh = !baseline;
      for (const task of next.tasks) {
        const old = before.get(task.id);
        if (old && old.owner && task.owner && old.owner !== task.owner)
          interact({ id: `handoff:${task.id}:${task.updated}`, at: task.updated, from: old.owner, to: task.owner, kind: 'handoff', task_id: task.id,
            task_title: task.title, from_owner: old.owner, to_owner: task.owner, text: clip(task.title, 140), fresh });
        // Each agent's latest image: the newest attachment on a task they own or updated.
        const known = new Set((old?.attachments ?? []).map(x => x.url));
        for (const image of task.attachments ?? []) {
          if (!IMAGE.test(image.url) || (old && known.has(image.url))) continue;
          const by = task.updates[task.updates.length - 1]?.by || task.owner;
          const a = agentFor(by) || agentFor(task.owner);
          if (a && (!a.artifact || a.artifact.at <= task.updated))
            a.artifact = { url: image.url, kind: image.source ? 'markup' : 'upload', task_id: task.id, at: task.updated, source_url: image.source, note: image.note };
        }
      }
      for (const review of reviews.values()) {
        const old = beforeReviews.get(review.id);
        if (!old && review.status === 'pending')
          interact({ id: `review:${review.id}:request`, at: review.created, from: review.requested_by, to: review.reviewer, kind: 'review_request', task_id: review.task_id, task_title: taskTitle(review.task_id), record_id: review.id, text: clip(review.request, 140), fresh: fresh && before.size > 0 });
        if (review.status === 'approved' || review.status === 'changes_requested') if (!old || old.status !== review.status)
          interact({ id: `review:${review.id}:${review.status}:${review.updated}`, at: review.updated, from: review.reviewer, to: review.owner || review.requested_by, kind: 'review_verdict', task_id: review.task_id, task_title: taskTitle(review.task_id), record_id: review.id,
            text: clip(`${review.status === 'approved' ? 'Approved' : 'Changes requested'}: ${review.findings}`, 140), fresh: fresh && !!old });
      }
      for (const f of followups.values()) {
        const old = beforeFollowups.get(f.id);
        if (!old && f.status === 'open')
          interact({ id: `followup:${f.id}`, at: f.created, from: f.created_by, to: f.assigned_to, kind: 'followup', task_id: f.task_id || undefined, task_title: taskTitle(f.task_id), record_id: f.id, text: clip(f.text, 140), fresh: fresh && before.size > 0 });
        if (f.status === 'resolved' && (!old || old.status !== 'resolved'))
          interact({ id: `followup:${f.id}:result`, at: f.updated, from: f.assigned_to, to: f.created_by, kind: 'followup_result', task_id: f.task_id || undefined, task_title: taskTitle(f.task_id), record_id: f.id, text: clip(f.result, 140), fresh: fresh && !!old });
      }
      for (const a of agents.values()) settleTask(a);
      publish();
    }

    // ---------- runtime turn events (both modes: speech; derived mode: state and activity) ----------
    function onRuntime(event: RoomEvent) {
      const d = event.data as RuntimeData;
      if (!d?.agent || !d.run) return;
      const a = agentFor(d.agent);
      if (!a) return;
      runs.set(d.run, d.agent);
      if (event.type === 'runtime_start') {
        if (source === 'derived') { setState(a, 'working'); a.activity = 'starting a turn'; settleTask(a, d.task_id); }
      } else if (event.type === 'runtime_delta' || event.type === 'runtime_draft') {
        const draft = event.type === 'runtime_draft';
        let s = streams.get(d.run);
        if (!s || s.message !== (d.message ?? '') || s.draft && !draft) { s = { message: d.message ?? '', text: '', draft }; streams.set(d.run, s); }
        s.draft = draft;
        if (d.sealed) { a.speech = ''; if (source === 'derived') a.activity = 'working on a team decision'; }
        else { s.text = (s.text + (d.text ?? '')).slice(-SPEECH_TAIL * 3); a.speech = tail(s.text, SPEECH_TAIL); a.speechAt = now();
          if (source === 'derived' && (!a.activity || a.activity === 'starting a turn')) a.activity = draft ? 'working' : 'writing a reply'; }
        if (source === 'derived') { if (a.state !== 'working' && a.state !== 'waiting_on_user') setState(a, 'working'); settleTask(a, d.task_id); }
      } else if (event.type === 'runtime_tool') {
        if (source === 'derived') a.activity = d.label || (d.tool ? `using ${d.tool}` : 'using a tool');
      } else if (event.type === 'runtime_end') {
        streams.delete(d.run); runs.delete(d.run); explicitTask.delete(d.agent);
        if (source === 'derived') {
          const failed = d.outcome && !['completed', 'cancelled', 'interrupted'].includes(d.outcome);
          setState(a, failed ? 'failed' : 'idle', failed ? `Turn ended: ${d.outcome}` : undefined);
          a.activity = failed ? 'turn failed' : '';
          settleTask(a);
        }
      }
      publish();
    }

    // ---------- chat: @mentions and shared images (derived mode) ----------
    function onMessage(event: RoomEvent) {
      if (source === 'server') return;
      const m = event.data as { id?: string | number; sender?: string; text?: string; channel?: string; project_id?: string; timestamp?: number; attachments?: { url: string; note?: string; source?: string }[] };
      if (!m?.sender || m.sender === 'system' || (m.project_id && project && m.project_id !== project)) return;
      const from = m.sender === 'user' ? 'user' : idFor(m.sender) || m.sender;
      if (from !== 'user' && !agents.has(from)) return;
      const at = m.timestamp || now();
      const targets = new Set<string>();
      for (const match of (m.text || '').matchAll(MENTION)) { const to = idFor(match[2]); if (to && to !== from) targets.add(to); }
      for (const to of targets) interact({ id: `mention:${m.id ?? at}:${to}`, at, channel: m.channel, from, to, kind: 'mention', text: clip(m.text || '', 140), fresh: ready });
      const image = (m.attachments ?? []).filter(x => IMAGE.test(x.url)).pop();
      const a = agentFor(from);
      if (image && a) { a.artifact = { url: image.url, kind: image.source ? 'markup' : 'upload', at, source_url: image.source, note: image.note }; publish(); }
    }

    // ---------- shared browser: the agent driving it shows the live frame (derived mode) ----------
    function onBrowser(event: RoomEvent) {
      if (source === 'server') return;
      const d = event.data as { project?: string; page?: string; activity?: { actor?: string; label?: string; action?: string; phase?: string; time?: number; page?: string } | null };
      const act = d?.activity;
      if (!act?.action || act.phase !== 'done' || ['status', 'inspect', 'release'].includes(act.action) || act.actor === 'user') return;
      const a = agentFor(idFor(act.label || '') || '');
      if (!a) return;
      a.artifact = { url: `/api/runtime/browser/frame?project=${encodeURIComponent(d.project || project)}`, kind: 'browser_frame', at: act.time || now(), page: act.page || d.page, frame_at: act.time };
      a.activity = act.action === 'screenshot' ? 'taking a browser screenshot' : 'using the shared browser';
      publish();
    }

    // ---------- server feed ----------
    function applyServerAgent(next: Omit<LiveAgent, 'color' | 'speech' | 'speechAt' | 'short'>) {
      const a = agents.get(next.id);
      // The feed has no handle; runtime_state (applied first) already set the stage name for this id.
      agents.set(next.id, { ...next, short: a?.short || shortName(next.name, next.id), color: a?.color || 'var(--accent)', speech: a?.speech || '', speechAt: a?.speechAt || 0 });
    }
    function onLive(event: RoomEvent) {
      if (source !== 'server') return;
      const d = event.data as { kind: 'agent'; agent: LiveAgent } | { kind: 'interaction'; interaction: LiveInteraction };
      if (d?.kind === 'agent') { applyServerAgent(d.agent); publish(); }
      else if (d?.kind === 'interaction') interact({ ...d.interaction, fresh: true });
    }

    function reset() {
      agents.clear(); interactions.length = 0; approvals = []; seenInteraction.clear(); runs.clear(); streams.clear(); openQuestions.clear(); explicitTask.clear();
      tasks = new Map(); reviews = new Map(); followups = new Map(); ready = false;
    }

    async function load() {
      const request = ++generation;
      ready = false; publish(true);
      try {
        const [current, board] = await Promise.all([runtime<RuntimeState>(''), runtime<ProjectState>('/project-state')]);
        if (stopped || request !== generation) return;
        reset();
        project = current.project_id || '';
        try {
          const live = await api<{ project_id: string; agents: Omit<LiveAgent, 'color' | 'speech' | 'speechAt' | 'short'>[]; interactions: LiveInteraction[] }>(`/api/live?project=${encodeURIComponent(project)}`);
          if (stopped || request !== generation) return;
          source = 'server';
          applyRoom(current);
          for (const agent of live.agents) applyServerAgent(agent);
          for (const item of live.interactions) interact({ ...item, fresh: false });
          applyProject(board, true);
        } catch (e) {
          // An older request that lost the race: a newer load owns the state (frontend d692c617 F3).
          if (stopped || request !== generation) return;
          // A different open project (409): the room switched under us; reload, and never derive from the
          // snapshot the server just rejected.
          if (e instanceof ApiError && e.status === 409) {
            if (++conflicts <= 3) { window.setTimeout(() => { if (!stopped) void load(); }, 400 * conflicts); return; }
            error = 'The open project keeps changing; reload Live to catch up.'; publish(true); return;
          }
          // Only a missing route (404, server not updated yet) falls back to deriving; anything else is reported.
          if (!(e instanceof ApiError && e.status === 404)) { error = `Live feed unavailable: ${errorMessage(e)}`; publish(true); return; }
          source = 'derived';
          applyRoom(current);
          applyProject(board, true);
          // Seed from history so a new Live view is never an empty stage.
          for (const a of agents.values()) {
            const latest = [...tasks.values()].filter(t => t.owner === a.id).flatMap(t => (t.attachments ?? []).filter(x => IMAGE.test(x.url)).map(x => ({ x, t })))
              .sort((p, q) => q.t.updated - p.t.updated)[0];
            if (latest && !a.artifact) a.artifact = { url: latest.x.url, kind: latest.x.source ? 'markup' : 'upload', task_id: latest.t.id, at: latest.t.updated, source_url: latest.x.source, note: latest.x.note };
          }
          const history: LiveInteraction[] = [];
          for (const r of reviews.values()) history.push({ id: `review:${r.id}:request`, at: r.created, from: r.requested_by, to: r.reviewer, kind: 'review_request', task_id: r.task_id, task_title: taskTitle(r.task_id), record_id: r.id, text: clip(r.request, 140) });
          for (const f of followups.values()) history.push({ id: `followup:${f.id}`, at: f.created, from: f.created_by, to: f.assigned_to, kind: 'followup', task_id: f.task_id || undefined, task_title: taskTitle(f.task_id), record_id: f.id, text: clip(f.text, 140) });
          history.sort((p, q) => q.at - p.at).slice(0, 40).forEach(interact);
        }
        ready = true; error = ''; conflicts = 0;
      } catch (e) {
        if (!stopped && request === generation) error = errorMessage(e);
      }
      publish(true);
    }

    const handlers: [string, (event: RoomEvent) => void][] = [
      ['runtime_state', e => applyRoom(e.data as RuntimeState)],
      ['project_state', e => applyProject(e.data as ProjectState, !ready)],
      ['runtime_start', onRuntime], ['runtime_delta', onRuntime], ['runtime_draft', onRuntime], ['runtime_tool', onRuntime], ['runtime_end', onRuntime],
      ['message', onMessage],
      ['browser_activity', onBrowser],
      ['live', onLive],
      ['settings', () => { void load(); }],
    ];
    const attach = () => { if (attached || !window.Hub) return; attached = true; handlers.forEach(([type, fn]) => window.Hub!.on(type, fn)); };
    attach();
    window.addEventListener('load', attach);
    void load();
    // Reconnects: reload the snapshot once the socket is back, so nothing missed while away is shown as new.
    let socket = window.ws, wasOpen = socket?.readyState === WebSocket.OPEN;
    const watch = window.setInterval(() => {
      attach();
      const open = window.ws?.readyState === WebSocket.OPEN;
      if (window.ws !== socket || open !== wasOpen) { socket = window.ws; if (open && !wasOpen) void load(); wasOpen = open; publish(); }
    }, 1000);
    return () => {
      stopped = true; generation++;
      if (timer !== null) window.clearTimeout(timer);
      window.clearInterval(watch);
      window.removeEventListener('load', attach);
      if (attached) handlers.forEach(([type, fn]) => window.Hub?.off(type, fn));
    };
  }, [active]);
  return feed;
}
