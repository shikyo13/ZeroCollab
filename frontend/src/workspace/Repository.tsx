/* Repository controls for the active project: the attached repositories (the primary one is
 * each agent's working folder; additional ones get their own worktrees), changing, adding,
 * promoting and removing them, a change in progress, an agent's room_repository request, and
 * the repositories used before. Every change keeps the board, chats, team, goal and queued
 * work; running turns finish first unless interrupted, and new turns wait for the worktrees. */
import { useEffect, useState, type ReactNode } from 'react';
import { runtime } from '../lib/api';
import { useNow, useRuntimeState } from '../lib/room';
import type { RepositoryTransition, RuntimeState } from '../lib/types';
import { ErrorText, RelativeTime, useAction } from '../board/shared';
import type { AttachedRepository, ProjectMember, ProjectSummary, RepositoryAction, RepositoryHistoryEntry, RepositoryRequest } from './projectData';

type TransitionAction = 'cancel' | 'interrupt';

/** What the open form does: path actions take a folder, key actions name an attached repository. */
type Pending = { action: 'replace' | 'add' } | { action: 'primary' | 'remove'; repo: AttachedRepository };

const BUSY_LABELS: Record<RepositoryAction, string> = { replace: 'Changing…', add: 'Adding…', primary: 'Switching…', remove: 'Removing…' };

const TRANSITION_TITLES: Record<RepositoryAction, string> = {
  replace: 'Changing repository', add: 'Adding repository', primary: 'Changing primary repository', remove: 'Removing repository',
};

function agentName(state: RuntimeState | null, handle: string): string {
  return state?.agents[handle]?.label ?? handle;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function parentFolder(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, '');
}

/** The folder holding an entry's agent worktrees, when they share one. */
function worktreeFolder(worktrees: Record<string, string>): string {
  const folders = new Set(Object.values(worktrees).map(parentFolder));
  return folders.size === 1 ? [...folders][0] : '';
}

/** Attached repositories; a server without multi-repository support reports only project.path. */
function attachedRepositories(project: ProjectSummary): AttachedRepository[] {
  if (project.repositories) return project.repositories;
  return project.path ? [{ key: '', path: project.path, primary: true }] : [];
}

/** Repositories this project used before and no longer has attached, most recent first, one row
 * each. Entries are matched by server key; entries recorded before keys fall back to the exact
 * path the server resolved (never case-folded, so distinct POSIX paths stay distinct). */
function previousRepositories(entries: RepositoryHistoryEntry[], attached: AttachedRepository[]): RepositoryHistoryEntry[] {
  const keys = new Set(attached.map(repo => repo.key).filter(Boolean));
  const paths = new Set(attached.map(repo => repo.path));
  const latest = new Map<string, RepositoryHistoryEntry>();
  for (const entry of entries) {
    if (entry.detached === null || (entry.key ? keys.has(entry.key) : paths.has(entry.path))) continue;
    const id = entry.key || `path:${entry.path}`;
    const seen = latest.get(id);
    if (!seen || (seen.detached ?? 0) < entry.detached) latest.set(id, entry);
  }
  return [...latest.values()].sort((a, b) => (b.detached ?? 0) - (a.detached ?? 0));
}

/** An agent request: add another repository, or replace the primary one. Adding to a project
 * with no repository yet is its first attachment, which the server applies the same way as
 * replace (the requested repository becomes primary), so it uses the first-attach form. */
function requestAction(request: RepositoryRequest, project: ProjectSummary): 'add' | 'replace' {
  return request.action === 'add' && project.path ? 'add' : 'replace';
}

function TransitionStatus({ project, transition, state }: { project: ProjectSummary; transition: RepositoryTransition; state: RuntimeState | null }) {
  const { run, busy, error } = useAction();
  const control = (action: TransitionAction) => void run(() => runtime(`/projects/${project.id}/repository/transition`, { action }));
  const waiting = transition.waiting_on.map(handle => agentName(state, handle)).join(', ');
  const step = transition.phase === 'draining'
    ? `Waiting for ${plural(transition.waiting_on.length, 'running turn')}${waiting ? ` (${waiting})` : ''}. New turns are held.`
    : 'Preparing a separate worktree for each agent.';
  return <div className="pj-notice" role="status">
    <strong className="bd-attention">{project.path ? TRANSITION_TITLES[transition.action ?? 'replace'] : TRANSITION_TITLES.add}</strong>
    {transition.path && <code className="pj-path pj-path-full">{transition.path}</code>}
    <span>{step}{transition.queued ? ` ${transition.queued} queued.` : ''}</span>
    {transition.cancel_requested
      ? <span className="bd-muted">Cancelling. The current repositories are kept.</span>
      : <div className="pj-actions">
        {transition.phase === 'draining' && transition.waiting_on.length > 0 && <button className="bd-btn" disabled={busy} onClick={() => control('interrupt')}>Interrupt now</button>}
        <button className="bd-btn" disabled={busy} onClick={() => control('cancel')}>Cancel change</button>
      </div>}
    <ErrorText message={error} />
  </div>;
}

function RequestCard({ project, request, state, onReview, onChanged }: { project: ProjectSummary; request: RepositoryRequest; state: RuntimeState | null; onReview: (() => void) | null; onChanged: () => void }) {
  const dismiss = useAction();
  const add = requestAction(request, project) === 'add';
  const replace = !add && (request.replace ?? !!project.path);
  return <div className="pj-notice">
    <strong className="bd-attention">{agentName(state, request.by)} requested {add ? 'an additional repository' : replace ? 'a repository change' : 'a repository'}</strong>
    <p>{request.reason}</p>
    {replace && request.current && <code className="pj-path pj-path-full">From {request.current}</code>}
    <code className="pj-path pj-path-full">{replace ? 'To ' : ''}{request.path}{request.create ? ' (new repository)' : ''}</code>
    <div className="pj-actions">
      {onReview && <button className="bd-btn bd-btn-primary" onClick={onReview}>Review request</button>}
      <button className="bd-btn" disabled={dismiss.busy} onClick={async () => { if (await dismiss.run(() => runtime(`/projects/${project.id}/repository/dismiss`, {}))) onChanged(); }}>Dismiss request</button>
    </div>
    <ErrorText message={dismiss.error} />
  </div>;
}

function DrainChoice({ running, interrupt, setInterrupt }: { running: number; interrupt: boolean; setInterrupt: (value: boolean) => void }) {
  return <fieldset className="pj-choice">
    <legend className="bd-muted">{plural(running, 'agent turn')} running</legend>
    <label><input type="radio" name="pj-drain" checked={!interrupt} onChange={() => setInterrupt(false)} /><span><strong>Let current turns finish</strong><small className="bd-muted">New turns wait and queued work is kept.</small></span></label>
    <label><input type="radio" name="pj-drain" checked={interrupt} onChange={() => setInterrupt(true)} /><span><strong>Interrupt current turns</strong><small className="bd-muted">Running turns stop now; queued work is kept.</small></span></label>
  </fieldset>;
}

function RepositoryRow({ repo, team, actions }: { repo: AttachedRepository; team: ProjectMember[]; actions: ReactNode }) {
  const branches = repo.primary ? team.filter(m => m.branch) : [];
  return <li className="pj-repo">
    <span className="pj-repo-head"><strong>{folderName(repo.path)}</strong>{repo.primary && <span className="bd-pill pj-active">Primary</span>}</span>
    <code className="pj-path pj-path-full">{repo.path}</code>
    {branches.length > 0 && <ul className="pj-branches">{branches.map(m => <li key={m.id}><strong>{m.name}</strong> <code>{m.branch}</code></li>)}</ul>}
    {actions}
  </li>;
}

function History({ entries, attached, state }: { entries: RepositoryHistoryEntry[]; attached: AttachedRepository[]; state: RuntimeState | null }) {
  const now = useNow();
  const previous = previousRepositories(entries, attached);
  if (!previous.length) return null;
  return <details className="pj-history">
    <summary className="bd-muted">Previous repositories ({previous.length})</summary>
    <ul>
      {previous.map(entry => {
        const worktrees = Object.entries(entry.worktrees);
        const folder = worktreeFolder(entry.worktrees);
        return <li key={entry.key || entry.path}>
          <code className="pj-path pj-path-full">{entry.path}</code>
          {entry.detached !== null && <RelativeTime className="bd-muted" time={entry.detached} now={now} prefix="Detached " />}
          {worktrees.length > 0 && <small className="bd-muted" title={worktrees.map(([handle, path]) => `${agentName(state, handle)}: ${path}`).join('\n')}>
            {plural(worktrees.length, 'agent worktree')} kept{folder ? <> in <code className="pj-path-full">{folder}</code></> : ''}
          </small>}
        </li>;
      })}
    </ul>
    <small className="bd-muted">Attaching one again reuses these worktrees.</small>
  </details>;
}

export function Repository({ project, team, onChanged }: { project: ProjectSummary; team: ProjectMember[]; onChanged: () => void }) {
  const { data: state } = useRuntimeState();
  const request = project.repo_request;
  const transition = state?.repository_transition ?? null;
  const running = state?.runs.length ?? 0;
  const repos = attachedRepositories(project);
  const primary = repos.find(repo => repo.primary);
  const extras = repos.length - (primary ? 1 : 0);
  // Only servers that report project.repositories accept add, primary and remove.
  const multi = project.repositories !== undefined && !!primary;
  const [pending, setPending] = useState<Pending | null>(null);
  const [path, setPath] = useState('');
  const [create, setCreate] = useState(false);
  const [interrupt, setInterrupt] = useState(false);
  const { run, busy, error } = useAction();
  useEffect(() => { setPending(null); setInterrupt(false); }, [project.id]);
  const open = (next: Pending, from?: RepositoryRequest) => {
    setPath(from?.path ?? '');
    setCreate(from?.create ?? false);
    setPending(next);
  };
  const submit = async () => {
    if (!pending) return;
    const body = 'repo' in pending
      ? { action: pending.action, key: pending.repo.key }
      : { action: pending.action, path: path.trim(), create };
    // The server answers once the change is committed; progress arrives as runtime_state.
    if (await run(() => runtime(`/projects/${project.id}/repository`, { ...body, interrupt: running > 0 && interrupt }))) {
      setPending(null);
      onChanged();
    }
  };
  const idle = !transition && !pending;
  const rowActions = (repo: AttachedRepository) => multi && !repo.primary && idle && <div className="pj-actions">
    <button className="bd-btn" onClick={() => open({ action: 'primary', repo })}>Make primary</button>
    <button className="bd-btn bd-btn-quiet" onClick={() => open({ action: 'remove', repo })}>Remove</button>
  </div>;

  let form: ReactNode = null;
  if (pending && !transition) {
    const action = pending.action;
    const byPath = !('repo' in pending);
    const name = 'repo' in pending ? folderName(pending.repo.path) : '';
    const note = {
      replace: `Board, chats, team, goal and queued work stay with this project. Each agent gets a separate worktree in this repository and starts a fresh CLI session there.${project.path ? ' Worktrees for the current repository stay on disk.' : ''}${extras ? ' Additional repositories stay attached.' : ''}`,
      add: 'Each agent gets a separate worktree in this repository as well; their working folder stays in the primary repository. Board, chats, team, goal and queued work are kept.',
      primary: `Each agent's working folder moves to its worktree of ${name}, with a fresh CLI session there.${primary ? ` ${folderName(primary.path)} stays attached as an additional repository.` : ''}`,
      remove: `${name} is detached from this project. Its worktrees stay on disk and are reused if you add it again.`,
    }[action];
    const label = {
      replace: create ? 'Create and use' : project.path ? 'Change repository' : 'Add repository',
      add: create ? 'Create and add' : 'Add repository',
      primary: `Make ${name} primary`,
      remove: `Remove ${name}`,
    }[action];
    form = <form className="pj-repo-form" onSubmit={e => { e.preventDefault(); void submit(); }}>
      {byPath && <>
        <label>Repository<select value={create ? 'create' : 'attach'} disabled={busy} onChange={e => setCreate(e.target.value === 'create')}><option value="attach">Attach existing repository</option><option value="create">Create a new repository</option></select></label>
        <label>Folder path<input required disabled={busy} value={path} onChange={e => setPath(e.target.value)} placeholder="Choose a local folder" /></label>
        <p className="bd-muted">{create ? 'Use a new or empty folder. Creates a local Git repository with an initial empty commit.' : 'Use the root folder of an existing Git repository.'}</p>
      </>}
      {running > 0 && <DrainChoice running={running} interrupt={interrupt} setInterrupt={setInterrupt} />}
      <p className="bd-muted">{note}</p>
      <div className="pj-actions">
        <button className="bd-btn bd-btn-primary" disabled={busy || !state || (byPath && !path.trim())}>{busy ? BUSY_LABELS[action] : label}</button>
        <button type="button" className="bd-btn" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
      </div>
    </form>;
  }

  return <div className="pj-repository">
    {transition && <TransitionStatus project={project} transition={transition} state={state} />}
    {request && !transition && <RequestCard project={project} request={request} state={state} onChanged={onChanged}
      onReview={pending ? null : () => open({ action: requestAction(request, project) }, request)} />}
    {repos.length > 0 && <ul className="pj-repos">{repos.map(repo => <RepositoryRow key={repo.key || repo.path} repo={repo} team={team} actions={rowActions(repo)} />)}</ul>}
    {idle && <div className="pj-actions">
      <button className="bd-btn" onClick={() => open({ action: 'replace' })}>{!project.path ? 'Add repository' : extras ? 'Change primary repository' : 'Change repository'}</button>
      {multi && <button className="bd-btn" onClick={() => open({ action: 'add' })}>Add repository</button>}
    </div>}
    {form}
    <ErrorText message={error} />
    <History entries={project.repository_history ?? []} attached={repos} state={state} />
  </div>;
}
