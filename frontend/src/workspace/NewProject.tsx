import { useEffect, useRef, useState } from 'react';
import { runtime } from '../lib/api';
import { useRuntimeState } from '../lib/room';
import { ErrorText, useAction } from '../board/shared';
import { LIMITS } from '../lib/constants';

/** A project exists before it needs a repository. Team choices copy profiles, not history. */
export function NewProject({ onCreated, onCancel }: {
  onCreated: (id: string) => void; onCancel: () => void;
}) {
  const { data: state } = useRuntimeState();
  const [name, setName] = useState('');
  const [team, setTeam] = useState<'current' | 'default'>('current');
  const { run, busy, error } = useAction();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  return <form className="bd-panel pj-new" aria-label="New project" onSubmit={async event => {
    event.preventDefault();
    let id = '';
    if (await run(async () => {
      const result = await runtime<{ id: string }>('/projects', { name: name.trim(), team });
      id = result.id;
    })) onCreated(id);
  }}>
    <h2>New project</h2>
    <p className="bd-muted">Start a conversation with its own team, board and goal. You can add a repository later.</p>
    <label>Project name<input ref={input} value={name} onChange={e => setName(e.target.value)} maxLength={LIMITS.name} required placeholder="What are you working on?" /></label>
    <fieldset disabled={busy}>
      <legend>Starting team</legend>
      <label className="pj-team-choice"><input type="radio" name="starting-team" checked={team === 'current'} onChange={() => setTeam('current')} />Copy current team</label>
      <label className="pj-team-choice"><input type="radio" name="starting-team" checked={team === 'default'} onChange={() => setTeam('default')} />Default team</label>
      <small className="bd-muted">Copies the selected profiles. You can change this team later.</small>
    </fieldset>
    <div className="pj-actions"><button className="bd-btn bd-btn-primary" disabled={busy || !state || !name.trim()}>{busy ? 'Creating…' : 'Create project'}</button><button className="bd-btn" type="button" disabled={busy} onClick={onCancel}>Cancel</button></div>
    <ErrorText message={error} />
  </form>;
}
