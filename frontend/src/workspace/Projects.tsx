/* Projects page: every repository or conversation the room has worked in, side by side.
 * Any project's board, team, goal and channels can be inspected without attaching it;
 * attaching (switching) is explicit because it stops the room and moves agent worktrees. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { since, useCoalesced, useNow, useRoomEvent, useRuntimeState } from '../lib/room';
import { TASK_STATUSES, type RuntimeState, type TaskStatus } from '../lib/types';
import { ErrorText, STATUS_LABELS, useAction } from '../board/shared';
import '../board/board.css';
import './projects.css';
import { NewProject } from './NewProject';
import { Repository } from './Repository';
import { openChannel, PROJECT_REFRESH_MS, projectLabel, roomIsBusy, switchProject, teamSignature, type ProjectDetail, type ProjectMember, type ProjectSummary } from './projectData';

export type ProjectNav = (view: 'chat' | 'board' | 'team') => void;

const PREVIEW_TASKS = 5;

function StatusBar({ counts, total }: { counts: Record<TaskStatus, number>; total: number }) {
  if (!total) return <div className="pj-bar pj-bar-empty" aria-hidden="true" />;
  return <div className="pj-bar" aria-hidden="true">
    {TASK_STATUSES.map(s => counts[s] ? <span key={s} className={`pj-seg pj-seg-${s}`} style={{ flexGrow: counts[s] }} /> : null)}
  </div>;
}

function countsText(project: ProjectSummary): string {
  const parts = TASK_STATUSES.filter(s => s !== 'done' && project.tasks[s]).map(s => `${project.tasks[s]} ${STATUS_LABELS[s].toLowerCase()}`);
  if (project.tasks.done) parts.push(`${project.tasks.done} done`);
  return parts.join(' · ') || 'No tasks yet';
}

function Avatars({ team }: { team: ProjectMember[] }) {
  const active = team.filter(m => !m.archived);
  return <span className="pj-avatars" aria-label={`${active.length} active agents`}>
    {active.slice(0, 6).map(m => <span key={m.id} className="pj-avatar" style={{ background: m.color }} title={m.name}>{m.name.slice(0, 2).toUpperCase()}</span>)}
    {active.length > 6 && <span className="pj-avatar pj-avatar-more">+{active.length - 6}</span>}
  </span>;
}

function ProjectCard({ project, selected, onSelect, now }: { project: ProjectSummary; selected: boolean; onSelect: () => void; now: number }) {
  return <li>
    <button className={`pj-card${selected ? ' pj-card-selected' : ''}`} aria-pressed={selected} onClick={onSelect}>
      <span className="pj-card-head">
        <strong>{project.name}</strong>
        {project.active && <span className="bd-pill pj-active">Active</span>}
        {!project.available && <span className="bd-pill pj-missing">Repo missing</span>}
      </span>
      <code className="pj-path" title={projectLabel(project)}>{projectLabel(project)}</code>
      <StatusBar counts={project.tasks} total={project.total_tasks} />
      <span className="pj-card-foot">
        <span>{countsText(project)}{project.pending_reviews ? ` · ${project.pending_reviews} review${project.pending_reviews > 1 ? 's' : ''} pending` : ''}</span>
        <Avatars team={project.team} />
      </span>
      <small className="bd-muted">{project.goal ? `Goal ${project.goal.status} · ` : ''}Active {since(project.updated, now)}</small>
    </button>
  </li>;
}

function Rename({ project, onDone }: { project: ProjectSummary; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const { run, busy, error } = useAction();
  useEffect(() => { setName(project.name); setEditing(false); }, [project.id, project.name]);
  if (!editing) return <button className="bd-btn bd-btn-quiet" onClick={() => setEditing(true)}>Rename</button>;
  return <form className="pj-rename" onSubmit={async e => {
    e.preventDefault();
    if (await run(() => runtime(`/projects/${project.id}`, { name }))) { setEditing(false); onDone(); }
  }}>
    <input value={name} onChange={e => setName(e.target.value)} maxLength={60} aria-label="Project name" autoFocus placeholder="Leave empty to use the folder name" />
    <button className="bd-btn" disabled={busy}>Save</button>
    <button type="button" className="bd-btn bd-btn-quiet" onClick={() => setEditing(false)}>Cancel</button>
    <ErrorText message={error} />
  </form>;
}

function SwitchButton({ project, onSwitched }: { project: ProjectSummary; onSwitched: () => void }) {
  const { data: state } = useRuntimeState();
  const [confirming, setConfirming] = useState(false);
  const { run, busy, error } = useAction();
  useEffect(() => setConfirming(false), [project.id]);
  if (!project.available && !project.conversation) return <p className="bd-attention">{project.path ? 'This repository folder no longer exists, so it cannot be attached.' : 'The repository path for this project could not be recovered. Attach the repository by its path below.'}</p>;
  const busyRoom = roomIsBusy(state);
  const go = async () => { if (await run(() => switchProject(project.id, state))) onSwitched(); };
  return <div className="pj-switch">
    {confirming
      ? <>
        <p>Switching stops every agent and cancels their current turns, then opens <strong>{project.name}</strong> with its own board and team.{state && !state.paused ? ' The team is resumed afterwards.' : ''}</p>
        <button className="bd-btn bd-btn-primary" disabled={busy} onClick={() => void go()}>{busy ? 'Switching…' : 'Stop agents and switch'}</button>
        <button className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
      </>
      : <button className="bd-btn bd-btn-primary" disabled={busy} onClick={() => busyRoom ? setConfirming(true) : void go()}>{busy ? 'Switching…' : 'Switch to this project'}</button>}
    <ErrorText message={error} />
  </div>;
}

function Detail({ id, onRenamed, navigate }: { id: string; onRenamed: () => void; navigate: ProjectNav }) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try { setDetail(await runtime<ProjectDetail>(`/projects/${id}`)); setError(''); }
    catch (e) { setError(errorMessage(e)); }
  }, [id]);
  useEffect(() => { setDetail(null); void load(); }, [load]);
  const loadSoon = useCoalesced(() => void load(), PROJECT_REFRESH_MS);
  const lastTeam = useRef('');
  useRoomEvent('project_state', () => { if (detail?.active) loadSoon(); });
  useRoomEvent('settings', () => { if (detail?.active) loadSoon(); });
  // runtime_state streams constantly; the detail only shows each member's state and branch.
  useRoomEvent<RuntimeState>('runtime_state', event => {
    if (!detail?.active) return;
    const team = teamSignature(event.data);
    if (team === lastTeam.current) return;
    lastTeam.current = team;
    loadSoon();
  });
  if (error) return <section className="bd-panel pj-detail"><ErrorText message={error} /></section>;
  if (!detail) return <section className="bd-panel pj-detail"><p className="bd-muted">Loading project…</p></section>;
  const board = detail.board;
  const reviews = (board.reviews ?? []).filter(r => r.status === 'pending');
  const followups = (board.followups ?? []).filter(f => f.status === 'open');
  const team = detail.team.filter(m => !m.archived);
  const archived = detail.team.length - team.length;
  const chat = (channel: string) => { if (!detail.active) return; openChannel(channel); navigate('chat'); };
  return <section className="pj-detail" aria-labelledby="pj-detail-title">
    <div className="bd-panel">
      <div className="pj-detail-head">
        <div>
          <h2 id="pj-detail-title">{detail.name}</h2>
          <code className="pj-path">{projectLabel(detail)}</code>
        </div>
        <Rename project={detail} onDone={() => { void load(); onRenamed(); }} />
      </div>
      {detail.active
        ? <div className="pj-actions">
          <span className="bd-pill pj-active">Active project</span>
          <button className="bd-btn" onClick={() => navigate('board')}>Open board</button>
          <button className="bd-btn" onClick={() => navigate('team')}>Open team</button>
          <button className="bd-btn" onClick={() => navigate('chat')}>Open chat</button>
        </div>
        : <SwitchButton project={detail} onSwitched={() => { onRenamed(); navigate('board'); }} />}
      {detail.goal && <p className="pj-goal"><strong>Goal · {detail.goal.status}</strong> {detail.goal.text} <span className="bd-muted">lead @{detail.goal.owner}</span></p>}
    </div>

    <div className="bd-panel">
      <div className="bd-panel-head"><h3>Board</h3>{detail.active && <button className="bd-btn bd-btn-quiet" onClick={() => navigate('board')}>Open full board</button>}</div>
      <p className="bd-muted">{countsText(detail)}{reviews.length ? ` · ${reviews.length} pending review${reviews.length > 1 ? 's' : ''}` : ''}{followups.length ? ` · ${followups.length} open follow-up${followups.length > 1 ? 's' : ''}` : ''}{board.spec ? ' · spec saved' : ''}</p>
      {detail.total_tasks > 0 && <div className="pj-columns">
        {TASK_STATUSES.map(status => {
          const tasks = board.tasks.filter(t => t.status === status).sort((a, b) => b.updated - a.updated);
          return <div key={status} className="pj-column">
            <h4 className={`bd-status-${status}`}>{STATUS_LABELS[status]} <span className="bd-badge">{tasks.length}</span></h4>
            <ul>{tasks.slice(0, PREVIEW_TASKS).map(t => <li key={t.id} title={t.details}><span>{t.title}</span><small className="bd-muted">{t.owner ? '@' + t.owner : 'Unclaimed'}</small></li>)}</ul>
            {tasks.length > PREVIEW_TASKS && <small className="bd-muted">+{tasks.length - PREVIEW_TASKS} more</small>}
          </div>;
        })}
      </div>}
    </div>

    <div className="pj-grid">
      <div className="bd-panel">
        <h3>Team</h3>
        {team.length ? <ul className="pj-team">{team.map(m => <li key={m.id}>
          <span className="pj-avatar" style={{ background: m.color }} aria-hidden="true">{m.name.slice(0, 2).toUpperCase()}</span>
          <span><strong>{m.name}</strong> <small className="bd-muted">@{m.id}</small>{m.state !== undefined && <small className="bd-muted"> · {m.paused ? 'stopped' : m.state.replaceAll('_', ' ')}</small>}</span>
        </li>)}</ul> : <p className="bd-muted">{detail.team.length ? 'Every agent in this project is archived.' : 'The default team is created when you first switch to this project.'}</p>}
        {archived > 0 && <small className="bd-muted">{archived} archived</small>}
      </div>
      <div className="bd-panel">
        <h3>Chats</h3>
        {detail.sessions?.length
          ? <div className="pj-channels">{detail.sessions.map(session => <button key={session.id} className="bd-btn" disabled={!detail.active} onClick={() => chat(session.id)}>{session.title}</button>)}</div>
          : <p className="bd-muted">No chat sessions yet.</p>}
        <small className="bd-muted">{detail.active ? 'Conversations in this project.' : 'Switch to this project to open its conversations.'}</small>
      </div>
      <div className="bd-panel">
        <h3>Repository</h3>
        {detail.active && <Repository project={detail} onChanged={() => { void load(); onRenamed(); }} />}
        {detail.conversation ? <p className="bd-muted">No repository. Agents work in private scratch folders; the board still works.</p> : <>
          <code className="pj-path pj-path-full">{detail.path || 'Unknown'}</code>
          {detail.active && <ul className="pj-branches">{team.filter(m => m.branch).map(m => <li key={m.id}><strong>{m.name}</strong> <code>{m.branch}</code></li>)}</ul>}
          {!detail.active && <small className="bd-muted">Each agent gets its own branch and worktree when this project is attached.</small>}
        </>}
      </div>
    </div>
  </section>;
}

export default function Projects({ projects, error, refresh, focus, setFocus, navigate }: {
  projects: ProjectSummary[]; error: string; refresh: () => Promise<void>;
  focus: string; setFocus: (id: string) => void; navigate: ProjectNav;
}) {
  const now = useNow();
  const [creating, setCreating] = useState(false);
  useEffect(() => { const open = () => setCreating(true); window.addEventListener("workspace-new-project", open); return () => window.removeEventListener("workspace-new-project", open); }, []);
  const selected = projects.find(p => p.id === focus) ?? projects.find(p => p.active) ?? projects[0];
  return <div className="bd-root pj-root">
    <header className="workspace-page-heading">
      <div>
        <span className="workspace-eyebrow">PROJECTS</span>
        <h1>Projects</h1>
        <p>Each project keeps its own conversations, board, team and goal. Start without a repository and add one when you need it.</p>
      </div>
      <button className="bd-btn bd-btn-primary" onClick={() => setCreating(true)}>New project</button>
    </header>
    <ErrorText message={error} />
    {creating && <NewProject onCancel={() => setCreating(false)} onCreated={id => { setCreating(false); setFocus(id); void refresh(); }} />}
    <div className="pj-layout">
      <div className="pj-side">
        <ul className="pj-list">{projects.map(p => <ProjectCard key={p.id} project={p} selected={p.id === selected?.id} onSelect={() => setFocus(p.id)} now={now} />)}</ul>
      </div>
      {selected ? <Detail key={selected.id} id={selected.id} onRenamed={() => void refresh()} navigate={navigate} /> : <p className="bd-muted">No projects yet. Choose New project to start a conversation.</p>}
    </div>
  </div>;
}
