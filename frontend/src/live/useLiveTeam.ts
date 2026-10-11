import { useEffect, useRef, useState } from 'react';
import { runtime, errorMessage } from '../lib/api';
import type { ProjectState, RoomEvent, RuntimeState, TaskStatus } from '../lib/types';
import { STATUS_LABELS } from '../board/shared';
import type { MotionCueData } from './MotionCue';

export interface LiveSource {
  id: string; actor: string; title: string; text: string; time: number; kind: string;
  task?: string; channel?: string; run?: string; revision?: string; to?: string; ref?: string;
  image?: string; count: number; fresh: boolean;
  motion?: MotionCueData;
}
export interface LiveModel {
  room: RuntimeState | null; project: ProjectState | null; sources: LiveSource[];
  connected: boolean; ready: boolean; received: number; error: string;
}
const empty = (): LiveModel => ({ room: null, project: null, sources: [], connected: false, ready: false, received: 0, error: '' });
const now = () => Date.now() / 1000;
const tail = (text: string) => text.replace(/\s+/g, ' ').trim().slice(-360);
export const uploadImage = (url?: string) => url && /^\/uploads\/[a-zA-Z0-9_-]+\.(png|jpe?g|webp|gif)$/i.test(url) ? url : undefined;

/** Current snapshots are a baseline, never replayed as new activity. All transient
 * sources are local to this view session. No tool arguments or inferred progress. */
export function useLiveTeam(active: boolean) {
  const [model, setModel] = useState<LiveModel>(empty);
  const refreshRef = useRef<() => void>(() => undefined);
  useEffect(() => {
    if (!active) return;
    let state = empty(), stopped = false, attached = false, dirty = false;
    let serial = 0, generation = 0, fetching = false, roomPush = 0, projectPush = 0;
    let socket: WebSocket | null | undefined;
    const ended = new Set<string>();
    const streams = new Map<string, { message: string; text: string }>();
    const publish = () => { dirty = true; };
    const add = (source: Omit<LiveSource, 'count' | 'fresh'>, fresh = true) => {
      const old = state.sources.find(s => s.id === source.id);
      if (old && !source.id.startsWith('run:')) return;
      state.sources = [{ ...source, count: (old?.count ?? 0) + 1, fresh }, ...state.sources.filter(s => s.id !== source.id)].slice(0, 40);
      state.received = now(); publish();
    };
    const taskSource = (task: ProjectState['tasks'][number], fresh: boolean, previousStatus?: TaskStatus) => {
      const last = task.updates[task.updates.length - 1];
      add({ id: `task:${task.id}:${task.updated}`, actor: last?.by || task.owner, title: task.title,
        text: last?.text || `Task is ${task.status}`, kind: 'Task', task: task.id, time: task.updated,
        motion: { version: 1, event: 'task_status', state: STATUS_LABELS[task.status], from: previousStatus && previousStatus !== task.status ? STATUS_LABELS[previousStatus] : undefined } }, fresh);
    };
    const reviewSource = (review: NonNullable<ProjectState['reviews']>[number], fresh: boolean) => {
      add({ id: `review:${review.id}:${review.updated}`, actor: review.status === 'pending' ? review.requested_by : review.reviewer,
        to: review.status === 'pending' ? review.reviewer : undefined, title: review.status === 'pending' ? `Review requested from ${review.reviewer}` : `Review ${review.status.replaceAll('_', ' ')}`,
        text: review.status === 'pending' ? review.request : review.findings || review.cancellation || review.status,
        kind: 'Review', ref: review.id, task: review.task_id, revision: review.revision, time: review.updated,
        motion: { version: 1, event: review.status === 'pending' ? 'review_request' : 'caption', state: review.status === 'pending' ? 'Awaiting review' : review.status.replaceAll('_', ' ') } }, fresh);
    };
    function project(next: ProjectState, baseline = false) {
      if (!Array.isArray(next?.tasks)) return;
      const previous = baseline ? null : state.project;
      if (previous) {
        for (const task of next.tasks) {
          const before = previous.tasks.find(t => t.id === task.id);
          if (!before || before.updated !== task.updated) taskSource(task, true, before?.status);
          for (const image of task.attachments ?? []) {
            const url = uploadImage(image.url);
            if (url && !before?.attachments?.some(a => a.url === url)) add({ id: `image:${task.id}:${url}`, actor: task.owner,
              kind: 'Task attachment', task: task.id, title: image.name, text: image.note || task.title, image: url, time: task.updated });
          }
          if (before?.owner && task.owner && before.owner !== task.owner) add({ id: `handoff:${task.id}:${task.updated}`, actor: before.owner, to: task.owner,
            title: 'Task ownership changed', text: task.title, task: task.id, kind: 'Handoff', time: task.updated,
            motion: { version: 1, event: 'caption', state: 'Ownership changed' } });
        }
        for (const review of next.reviews ?? []) {
          if (!previous.reviews?.some(r => r.id === review.id && r.updated === review.updated)) reviewSource(review, true);
        }
        for (const followup of next.followups ?? []) {
          if (!previous.followups?.some(f => f.id === followup.id && f.updated === followup.updated)) add({ id: `followup:${followup.id}:${followup.updated}`, actor: followup.status === 'open' ? followup.created_by : followup.assigned_to,
            to: followup.status === 'open' ? followup.assigned_to : undefined, task: followup.task_id, ref: followup.id, title: `Follow-up ${followup.status}`, text: followup.result || followup.text, kind: 'Follow-up', time: followup.updated });
        }
      } else {
        next.tasks.filter(t => t.updates.length).sort((a, b) => b.updated - a.updated).slice(0, 8).forEach(t => taskSource(t, false));
        (next.reviews ?? []).slice().sort((a, b) => b.updated - a.updated).slice(0, 6).forEach(r => reviewSource(r, false));
        // Show available finished evidence without animating it as newly published.
        for (const task of next.tasks) for (const image of task.attachments ?? []) {
          const url = uploadImage(image.url);
          if (url) add({ id: `image:${task.id}:${url}`, actor: task.owner, kind: 'Task attachment', task: task.id,
            title: image.name, text: image.note || task.title, image: url, time: task.updated }, false);
        }
        state.sources.sort((a, b) => b.time - a.time);
        state.sources = state.sources.slice(0, 40);
      }
      state.project = next; state.received = now(); publish();
    }
    function room(next: RuntimeState) {
      if (!next?.agents || !Array.isArray(next.runs)) return;
      const before = new Set(state.room?.runs.map(r => r.id) ?? []);
      const runs = next.runs.filter(r => !ended.has(r.id));
      const current = new Set(runs.map(r => r.id));
      for (const id of before) if (!current.has(id)) { ended.add(id); streams.delete(id); }
      // Keep the tombstones bounded; deltas cannot create an unknown running turn.
      if (ended.size > 500) { const keep = [...ended].slice(-250); ended.clear(); keep.forEach(id => ended.add(id)); }
      state.room = { ...next, runs }; state.received = now(); publish();
    }
    async function refresh() {
      if (fetching || stopped) return;
      fetching = true; state.ready = false; publish();
      const request = generation, roomVersion = roomPush, projectVersion = projectPush;
      try {
        const [current, board] = await Promise.all([runtime<RuntimeState>(''), runtime<ProjectState>('/project-state')]);
        if (stopped || request !== generation) return;
        // A pushed snapshot arriving during the fetch is newer than its response.
        if (roomVersion === roomPush) room(current);
        if (projectVersion === projectPush) project(board, true);
        state.ready = true; state.error = ''; state.connected = window.ws?.readyState === WebSocket.OPEN; publish();
      } catch (e) { if (!stopped && request === generation) { state.error = errorMessage(e); publish(); } }
      finally { fetching = false; if (!stopped && request !== generation && window.ws?.readyState === WebSocket.OPEN) void refresh(); }
    }
    refreshRef.current = () => { void refresh(); };
    const accept = () => state.ready && state.connected;
    const handlers: [string, (event: RoomEvent) => void][] = [
      ['settings', () => { generation++; state.sources = state.sources.map(s => ({ ...s, fresh: false })); state.ready = false; void refresh(); }],
      ['runtime_state', e => { roomPush++; const next = e.data as RuntimeState;
        if (state.room?.project_id && next.project_id && state.room.project_id !== next.project_id) { state = empty(); ended.clear(); streams.clear(); generation++; void refresh(); return; }
        room(next);
      }],
      ['project_state', e => { projectPush++; project(e.data as ProjectState, !state.ready); }],
      ...(['runtime_start', 'runtime_delta', 'runtime_tool', 'runtime_end'] as const).map(type => [type, (event: RoomEvent) => {
        if (!accept() || !state.room) return;
        const d = event.data as { run: string; agent: string; channel?: string; message?: string; text?: string; tool?: string; status?: string; outcome?: string };
        if (!d.run || !d.agent || ended.has(d.run)) return;
        const run = state.room.runs.find(r => r.id === d.run);
        if (type !== 'runtime_start' && type !== 'runtime_end' && !run) return;
        if (type === 'runtime_start') {
          // Only an explicit start or current snapshot can create a live run.
          if (!run) state.room = { ...state.room, runs: [...state.room.runs, { id: d.run, agent: d.agent, channel: d.channel || '', text: '', settings: {}, reported_settings: {}, reported_tokens: 0, input_chars: 0 }] };
          add({ id: `run:${d.run}`, actor: d.agent, channel: d.channel, run: d.run, title: 'Started a turn', text: 'Waiting for the next visible action.', kind: 'Turn', time: now(), motion: { version: 1, event: 'wake', state: 'Running' } });
        } else if (type === 'runtime_delta') {
          let stream = streams.get(d.run);
          if (!stream || stream.message !== (d.message ?? '')) stream = { message: d.message ?? '', text: '' };
          stream.text = (stream.text + (d.text || '')).slice(-1080); streams.set(d.run, stream);
          add({ id: `run:${d.run}`, actor: d.agent, channel: d.channel, run: d.run, title: 'Writing a reply', text: tail(stream.text), kind: 'Reply', time: now(), motion: { version: 1, event: 'caption', state: 'Writing a reply' } });
        } else if (type === 'runtime_tool') {
          add({ id: `run:${d.run}`, actor: d.agent, channel: d.channel, run: d.run, title: d.tool || 'Using a tool', text: d.status || 'Tool activity reported', kind: 'Tool', time: now(),
            motion: { version: 1, event: ['completed', 'failed', 'cancelled'].includes(d.status || '') ? 'tool_end' : ['started', 'running'].includes(d.status || '') ? 'tool_start' : 'caption', state: d.status || 'Tool activity reported' } });
        } else {
          ended.add(d.run); streams.delete(d.run);
          state.room = { ...state.room, runs: state.room.runs.filter(r => r.id !== d.run) };
          const last = state.sources.find(s => s.run === d.run);
          add({ id: `run:${d.run}`, actor: d.agent, channel: last?.channel || d.channel, run: d.run, title: `Turn ${d.outcome || 'ended'}`, text: last?.text || 'No visible reply was reported.', kind: 'Turn', time: now(), motion: { version: 1, event: 'caption', state: `Turn ${d.outcome || 'ended'}` } });
        }
      }] as [string, (event: RoomEvent) => void]),
      ['message', event => {
        if (!accept()) return;
        const d = event.data as { id?: string | number; sender?: string; text?: string; channel?: string; project_id?: string; timestamp?: number; attachments?: {url: string}[] };
        if (!d.sender || d.sender === 'system' || d.project_id && state.room?.project_id && d.project_id !== state.room.project_id) return;
        const image = d.attachments?.map(a => uploadImage(a.url)).find(Boolean);
        add({ id: `message:${d.id ?? ++serial}`, ref: d.id === undefined ? undefined : String(d.id), actor: d.sender, channel: d.channel, title: image ? 'Shared an artifact' : 'Posted a message', text: tail(d.text || ''), image, kind: 'Chat', time: d.timestamp || now(), motion: { version: 1, event: 'caption', state: image ? 'Shared artifact' : 'Message received' } });
      }],
      ['browser_activity', event => {
        if (!accept()) return;
        const d = event.data as { activity?: {actor: string; label: string; action: string; phase: string; time: number} };
        const a = d.activity;
        if (!a || ['status', 'inspect'].includes(a.action)) return;
        add({ id: `browser:${a.actor}:${a.time}:${a.action}:${a.phase}`, actor: a.label || 'Unknown', title: `Browser ${a.action}`, text: a.phase === 'working' ? 'Action in progress' : a.phase === 'failed' ? 'Action failed' : `Action ${a.phase}`, kind: 'Browser', time: a.time,
          motion: { version: 1, event: 'browser_emphasis', state: `Action ${a.phase} · target not reported` } });
      }],
    ];
    const attach = () => { if (!window.Hub || attached) return; attached = true; handlers.forEach(([type, fn]) => window.Hub!.on(type, fn)); };
    attach(); window.addEventListener('load', attach);
    void refresh();
    const timer = window.setInterval(() => {
      attach();
      const connected = window.ws?.readyState === WebSocket.OPEN;
      if (socket !== window.ws || connected !== state.connected) {
        const was = state.connected; socket = window.ws; state.connected = connected;
        if (!connected) { state.ready = false; generation++; state.sources = state.sources.map(s => ({...s, fresh: false})); }
        else if (!was) void refresh();
        publish();
      }
      if (dirty) { dirty = false; setModel({ ...state, sources: [...state.sources] }); }
    }, 200);
    return () => { stopped = true; generation++; clearInterval(timer); window.removeEventListener('load', attach); if (attached) handlers.forEach(([type, fn]) => window.Hub?.off(type, fn)); refreshRef.current = () => undefined; };
  }, [active]);
  return { ...model, refresh: () => refreshRef.current() };
}
