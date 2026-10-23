/* Project index (project_registry.py): every repository or conversation this room has
 * worked in, with its board counts, team, goal and channels. One project is attached
 * at a time; switching stops the room because each agent's worktree belongs to it. */
import { useCallback, useEffect, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { useCoalesced, useRoomEvent } from '../lib/room';
import type { Followup, Note, Review, RuntimeState, Task, TaskStatus } from '../lib/types';

export interface ProjectMember {
  id: string;
  name: string;
  color: string;
  template: string;
  archived: boolean;
  state?: string;
  paused?: boolean;
  branch?: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  path: string;
  active: boolean;
  /** Archived projects retain their data and remain available for browsing and Restore. */
  archived?: boolean;
  conversation: boolean;
  available: boolean;
  tasks: Record<TaskStatus, number>;
  total_tasks: number;
  pending_reviews: number;
  open_followups: number;
  goal: { id: string; text: string; status: string; owner: string; channel: string } | null;
  channels: string[];
  sessions: { id: string; title: string; readonly: boolean }[];
  last_session?: string;
  repo_request?: RepositoryRequest | null;
  /** Every repository attached to the project; absent from servers without multi-repository support. */
  repositories?: AttachedRepository[];
  repository_history?: RepositoryHistoryEntry[];
  team: ProjectMember[];
  spec: boolean;
  updated: number;
}

/** POST /projects/{id}/repository actions. replace swaps the primary repository, add attaches
 * another one, primary promotes an attached one (the old primary stays attached), remove
 * detaches an additional one. Every action drains or interrupts running turns first. */
export type RepositoryAction = 'replace' | 'add' | 'primary' | 'remove';

/** key is the server's stable repository key; compare repositories by key, never by path text. */
export interface AttachedRepository {
  key: string;
  path: string;
  primary: boolean;
}

/** An agent's room_repository proposal. Older servers send replace instead of action. */
export interface RepositoryRequest {
  by: string;
  path: string;
  create: boolean;
  reason: string;
  time: number;
  action?: 'add' | 'replace';
  replace?: boolean;
  current?: string;
}

/** One repository this project has used. attached is null for a repository recorded from
 * before history was kept; detached is null while it is attached. Worktrees are kept on disk
 * and reused when the project switches back. key is absent on entries from before keys. */
export interface RepositoryHistoryEntry {
  key?: string;
  path: string;
  attached: number | null;
  detached: number | null;
  by?: string;
  worktrees: Record<string, string>;
}

/** Board edits arrive as bursts of project_state frames; re-read at most this often. */
export const PROJECT_REFRESH_MS = 500;

export interface ProjectDetail extends ProjectSummary {
  board: { spec: string; tasks: Task[]; notes: Note[]; reviews?: Review[]; followups?: Followup[] };
}

/** Live project index; re-read when the attached project or its board changes. */
export function useProjects() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const result = await runtime<{ projects: ProjectSummary[] }>('/projects');
      setProjects(result.projects);
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const refreshSoon = useCoalesced(() => void refresh(), PROJECT_REFRESH_MS);
  useRoomEvent('project_state', refreshSoon);
  useRoomEvent('project_delta', refreshSoon);
  useRoomEvent('settings', () => void refresh());
  // runtime_state fires often (agent state, streaming); only a new project needs a re-read.
  const [attached, setAttached] = useState<string | null>(null);
  useRoomEvent<RuntimeState>('runtime_state', event => setAttached(event.data.project_id ?? event.data.project ?? ''));
  useEffect(() => { if (attached !== null) void refresh(); }, [attached, refresh]);
  return { projects, error, loaded, refresh, active: projects.find(p => p.active) };
}

/** Changes only when an agent's visible team state does, not on every streamed runtime frame. */
export function teamSignature(state: RuntimeState): string {
  return Object.entries(state.agents)
    .map(([id, agent]) => `${id}:${agent.state}:${agent.paused}:${agent.branch ?? ''}:${agent.archived}`)
    .sort()
    .join('|');
}

export function projectLabel(project: ProjectSummary): string {
  return project.conversation ? 'No repository - chat and board only' : project.path || 'Repository path unknown';
}

/** True when switching would interrupt work: the server only switches a stopped, idle room. */
export function roomIsBusy(state: RuntimeState | null): boolean {
  if (!state) return true;
  return !state.paused || state.runs.length > 0 || Object.values(state.agents).some(a => a.queued > 0);
}

/** Attach a project, stopping the room first if needed. A room that was running is resumed
 * afterwards so the new project's team is ready; nothing is queued, so no turn starts by itself. */
export async function switchProject(id: string, state: RuntimeState | null): Promise<void> {
  const wasRunning = !!state && !state.paused;
  if (roomIsBusy(state)) await runtime('/stop', {});
  try { await runtime('/project', { id }); }
  finally { if (wasRunning) await runtime('/resume', {}); }
}

export function openChannel(channel: string) {
  const w = window as unknown as { switchChannel?: (name: string) => void };
  w.switchChannel?.(channel);
}
