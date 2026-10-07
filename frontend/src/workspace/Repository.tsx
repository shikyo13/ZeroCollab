import { useEffect, useState } from 'react';
import { runtime } from '../lib/api';
import { useRuntimeState } from '../lib/room';
import { ErrorText, useAction } from '../board/shared';
import { roomIsBusy, type ProjectSummary } from './projectData';

export function Repository({ project, onChanged }: { project: ProjectSummary; onChanged: () => void }) {
  const { data: state } = useRuntimeState();
  const request = project.repo_request;
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState('');
  const [create, setCreate] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const { run, busy, error } = useAction();
  useEffect(() => { setPath(request?.path ?? ''); setCreate(request?.create ?? false); setConfirmed(false); }, [project.id, request?.path, request?.create]);
  if (project.path) return null;
  return <div className="pj-repository">
    {request && <div className="bd-attention"><strong>{request.by} requested a repository</strong><p>{request.reason}</p><code className="pj-path pj-path-full">{request.path}</code><button className="bd-btn" disabled={busy} onClick={async () => { if (await run(() => runtime(`/projects/${project.id}/repository/dismiss`, {}))) onChanged(); }}>Dismiss request</button></div>}
    {!editing ? <button className="bd-btn" onClick={() => setEditing(true)}>{request ? "Review repository request" : "Add repository"}</button> : <form className="pj-repo-form" onSubmit={async e => {
      e.preventDefault();
      if (roomIsBusy(state) && !confirmed) { setConfirmed(true); return; }
      if (await run(async () => {
        const wasRunning = !!state && !state.paused;
        if (roomIsBusy(state)) await runtime('/stop', {});
        try { await runtime(`/projects/${project.id}/repository`, { path: path.trim(), create }); }
        finally { if (wasRunning) await runtime('/resume', {}); }
      })) { setEditing(false); setConfirmed(false); onChanged(); }
    }}>
      <label>Repository<select value={create ? 'create' : 'attach'} onChange={e => { setCreate(e.target.value === 'create'); setConfirmed(false); }}><option value="attach">Attach existing repository</option><option value="create">Create a new repository</option></select></label>
      <label>Folder path<input required value={path} onChange={e => { setPath(e.target.value); setConfirmed(false); }} placeholder="Choose a local folder" /></label>
      <p className="bd-muted">{create ? 'Use a new or empty folder. Creates a local Git repository with an initial empty commit.' : 'Use the root folder of an existing Git repository.'} Conversations, board and team stay with this project.</p>
      {confirmed && <p role="status" className="bd-attention">This stops the room's agents and cancels current turns to set up their worktrees.</p>}
      <div className="pj-actions"><button className="bd-btn bd-btn-primary" disabled={busy || !state || !path.trim()}>{busy ? 'Adding…' : confirmed ? 'Stop agents and add repository' : create ? 'Create and attach' : 'Attach repository'}</button><button type="button" className="bd-btn" disabled={busy} onClick={() => { setEditing(false); setConfirmed(false); }}>Cancel</button></div>
    </form>}
    <ErrorText message={error} />
  </div>;
}
