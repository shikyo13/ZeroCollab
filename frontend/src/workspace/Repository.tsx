/* Repository controls for the active project: add or change its repository, follow a change
 * in progress, answer an agent's room_repository request, and list the repositories it used
 * before. A change keeps the board, chats, team, goal and queued work; running turns finish
 * first unless interrupted, and new turns wait until the agents' worktrees are ready. */
import { useEffect, useState } from 'react';
import { runtime } from '../lib/api';
import { useNow, useRuntimeState } from '../lib/room';
import type { RepositoryTransition, RuntimeState } from '../lib/types';
import { ErrorText, RelativeTime, useAction } from '../board/shared';
import type { ProjectSummary, RepositoryHistoryEntry } from './projectData';

type TransitionAction = 'cancel' | 'interrupt';

function agentName(state: RuntimeState | null, handle: string): string {
  return state?.agents[handle]?.label ?? handle;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function TransitionStatus({ project, transition, state }: { project: ProjectSummary; transition: RepositoryTransition; state: RuntimeState | null }) {
  const { run, busy, error } = useAction();
  const control = (action: TransitionAction) => void run(() => runtime(`/projects/${project.id}/repository/transition`, { action }));
  const waiting = transition.waiting_on.map(handle => agentName(state, handle)).join(', ');
  const step = transition.phase === 'draining'
    ? `Waiting for ${plural(transition.waiting_on.length, 'running turn')}${waiting ? ` (${waiting})` : ''}. New turns are held.`
    : 'Preparing a separate worktree for each agent.';
  return <div className="pj-notice" role="status">
    <strong className="bd-attention">{project.path ? 'Changing repository' : 'Adding repository'}</strong>
    {transition.path && <code className="pj-path pj-path-full">{transition.path}</code>}
    <span>{step}{transition.queued ? ` ${transition.queued} queued.` : ''}</span>
    {transition.cancel_requested
      ? <span className="bd-muted">Cancelling. The current repository is kept.</span>
      : <div className="pj-actions">
        {transition.phase === 'draining' && transition.waiting_on.length > 0 && <button className="bd-btn" disabled={busy} onClick={() => control('interrupt')}>Interrupt now</button>}
        <button className="bd-btn" disabled={busy} onClick={() => control('cancel')}>Cancel change</button>
      </div>}
    <ErrorText message={error} />
  </div>;
}

/** Paths come from one server-side resolver; Windows paths compare case-insensitively. */
function samePath(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

function parentFolder(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, '');
}

/** The folder holding an entry's agent worktrees, when they share one. */
function worktreeFolder(worktrees: Record<string, string>): string {
  const folders = new Set(Object.values(worktrees).map(parentFolder));
  return folders.size === 1 ? [...folders][0] : '';
}

/** Repositories this project used before, most recent first, one row per path. A path that
 * is attached again is the current repository, not a previous one. */
function previousRepositories(entries: RepositoryHistoryEntry[], current: string): RepositoryHistoryEntry[] {
  const latest = new Map<string, RepositoryHistoryEntry>();
  for (const entry of entries) {
    if (entry.detached === null || (current && samePath(entry.path, current))) continue;
    const key = entry.path.toLowerCase();
    const seen = latest.get(key);
    if (!seen || (seen.detached ?? 0) < entry.detached) latest.set(key, entry);
  }
  return [...latest.values()].sort((a, b) => (b.detached ?? 0) - (a.detached ?? 0));
}

function History({ entries, current, state }: { entries: RepositoryHistoryEntry[]; current: string; state: RuntimeState | null }) {
  const now = useNow();
  const previous = previousRepositories(entries, current);
  if (!previous.length) return null;
  return <details className="pj-history">
    <summary className="bd-muted">Previous repositories ({previous.length})</summary>
    <ul>
      {previous.map(entry => {
        const worktrees = Object.entries(entry.worktrees);
        const folder = worktreeFolder(entry.worktrees);
        return <li key={entry.path}>
          <code className="pj-path pj-path-full">{entry.path}</code>
          {entry.detached !== null && <RelativeTime className="bd-muted" time={entry.detached} now={now} prefix="Replaced " />}
          {worktrees.length > 0 && <small className="bd-muted" title={worktrees.map(([handle, path]) => `${agentName(state, handle)}: ${path}`).join('\n')}>
            {plural(worktrees.length, 'agent worktree')} kept{folder ? <> in <code className="pj-path-full">{folder}</code></> : ''}
          </small>}
        </li>;
      })}
    </ul>
    <small className="bd-muted">Switching back reuses these worktrees.</small>
  </details>;
}

export function Repository({ project, onChanged }: { project: ProjectSummary; onChanged: () => void }) {
  const { data: state } = useRuntimeState();
  const request = project.repo_request;
  const transition = state?.repository_transition ?? null;
  const running = state?.runs.length ?? 0;
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState('');
  const [create, setCreate] = useState(false);
  const [interrupt, setInterrupt] = useState(false);
  const { run, busy, error } = useAction();
  const dismiss = useAction();
  useEffect(() => { setPath(request?.path ?? ''); setCreate(request?.create ?? false); }, [project.id, request?.path, request?.create]);
  useEffect(() => { setEditing(false); setInterrupt(false); }, [project.id]);
  const change = !!project.path;
  return <div className="pj-repository">
    {transition && <TransitionStatus project={project} transition={transition} state={state} />}
    {request && !transition && <div className="pj-notice">
      <strong className="bd-attention">{agentName(state, request.by)} requested {request.replace ? 'a repository change' : 'a repository'}</strong>
      <p>{request.reason}</p>
      {request.replace && request.current && <code className="pj-path pj-path-full">From {request.current}</code>}
      <code className="pj-path pj-path-full">{request.replace ? 'To ' : ''}{request.path}{request.create ? ' (new repository)' : ''}</code>
      <div className="pj-actions">
        {!editing && <button className="bd-btn bd-btn-primary" onClick={() => setEditing(true)}>Review request</button>}
        <button className="bd-btn" disabled={dismiss.busy} onClick={async () => { if (await dismiss.run(() => runtime(`/projects/${project.id}/repository/dismiss`, {}))) onChanged(); }}>Dismiss request</button>
      </div>
      <ErrorText message={dismiss.error} />
    </div>}
    {/* While a change runs, its banner replaces the form; a failed change brings the form back with the error. */}
    {transition ? null : !editing
      ? <button className="bd-btn" onClick={() => setEditing(true)}>{change ? 'Change repository' : 'Add repository'}</button>
      : <form className="pj-repo-form" onSubmit={async e => {
        e.preventDefault();
        // The server answers once the change is committed; progress arrives as runtime_state.
        if (await run(() => runtime(`/projects/${project.id}/repository`, { path: path.trim(), create, interrupt: running > 0 && interrupt }))) {
          setEditing(false);
          onChanged();
        }
      }}>
        <label>Repository<select value={create ? 'create' : 'attach'} disabled={busy} onChange={e => setCreate(e.target.value === 'create')}><option value="attach">Attach existing repository</option><option value="create">Create a new repository</option></select></label>
        <label>Folder path<input required disabled={busy} value={path} onChange={e => setPath(e.target.value)} placeholder="Choose a local folder" /></label>
        <p className="bd-muted">{create ? 'Use a new or empty folder. Creates a local Git repository with an initial empty commit.' : 'Use the root folder of an existing Git repository.'}</p>
        {running > 0 && <fieldset className="pj-choice" disabled={busy}>
          <legend className="bd-muted">{plural(running, 'agent turn')} running</legend>
          <label><input type="radio" name="pj-drain" checked={!interrupt} onChange={() => setInterrupt(false)} /><span><strong>Let current turns finish</strong><small className="bd-muted">New turns wait and queued work is kept.</small></span></label>
          <label><input type="radio" name="pj-drain" checked={interrupt} onChange={() => setInterrupt(true)} /><span><strong>Interrupt current turns</strong><small className="bd-muted">Running turns stop now; queued work is kept.</small></span></label>
        </fieldset>}
        <p className="bd-muted">Board, chats, team, goal and queued work stay with this project. Each agent gets a separate worktree in this repository and starts a fresh CLI session there.{change ? ' Worktrees for the current repository stay on disk.' : ''}</p>
        <div className="pj-actions">
          <button className="bd-btn bd-btn-primary" disabled={busy || !state || !!transition || !path.trim()}>{busy ? (change ? 'Changing…' : 'Adding…') : create ? 'Create and use' : change ? 'Change repository' : 'Add repository'}</button>
          <button type="button" className="bd-btn" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </form>}
    <ErrorText message={error} />
    <History entries={project.repository_history ?? []} current={project.path} state={state} />
  </div>;
}
