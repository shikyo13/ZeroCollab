import { useId } from 'react';
import { matchesDraft } from './roleProfileApi';
import { useRoleProfile } from './useRoleProfile';
import './roleProfile.css';

export function RoleProfile({ name, guarded = false }: { name: string; guarded?: boolean }) {
  const id = useId();
  const { view, busy, error, note, run } = useRoleProfile(name);
  const draft = view?.draft;
  const current = view?.current;
  const approved = !!(draft && current && matchesDraft(current, draft));
  const disabled = !!busy || guarded;
  return <section className="tm-role-profile" aria-labelledby={id} aria-busy={!!busy}>
    <div className="tm-role-head">
      <h3 id={id}>Role profile</h3>
      <span className={current?.active ? 'tm-tone-ok' : 'bd-muted'}>
        {view ? current?.active ? `Active · v${current.core_version}` : 'Free-text background active'
          : !busy && error ? 'Role profile unavailable' : 'Loading role profile…'}
      </span>
    </div>
    {current?.approved_at && <p className="bd-muted">
      Approved by {current.approved_by} · <time dateTime={current.approved_at}>{current.approved_at}</time>
      {!current.active && ' · not active'}
    </p>}
    {current?.active && <details className="tm-role-current">
      <summary>Approved core · {current.skills?.length ?? 0} pinned skills</summary>
      <pre className="tm-role-core" tabIndex={0} aria-label="Approved role core">{current.core}</pre>
    </details>}
    {draft ? <>
      <p className="bd-muted">Review this core and the exact skill versions before approving.
        {current?.active && !approved && ' This draft will replace the active role profile.'}</p>
      <h4>Draft v{draft.core_version} · {draft.words.toLocaleString()} words</h4>
      <pre key={draft.digest} className="tm-role-core" tabIndex={0} aria-label="Draft role core">{draft.core}</pre>
      <h4>Pinned skills</h4>
      {draft.skill_details.length ? <ul className="tm-role-skills">
        {draft.skill_details.map(skill => <li key={skill.name}>
          <strong>{skill.name}</strong> <span className="bd-muted">v{skill.version}</span>
          <p>{skill.description}</p>
        </li>)}
      </ul> : <p className="bd-muted">No skills listed in this draft.</p>}
    </> : view && <p className="bd-muted">{view.draft_error || 'No drafted role profile is available.'}</p>}
    {guarded && <p className="bd-muted">Save or cancel your agent edits before changing the role profile.</p>}
    {error && <p className="bd-error" role="alert">{error}</p>}
    {note && <p className="tm-role-note" role="status">{note}</p>}
    <div className="bd-actions">
      {draft && <button type="button" className="bd-btn bd-btn-primary" disabled={disabled || approved}
        onClick={() => void run('approve')}>
        {busy === 'approve' ? 'Approving…' : approved ? 'This draft is approved' : 'Approve role profile'}
      </button>}
      {current?.active && <button type="button" className="bd-btn" disabled={disabled}
        onClick={() => void run('revoke')}>{busy === 'revoke' ? 'Revoking…' : 'Revoke role profile'}</button>}
      <button type="button" className="bd-btn bd-btn-quiet" disabled={!!busy} onClick={() => void run('refresh')}>
        {busy === 'refresh' ? 'Refreshing…' : 'Refresh draft'}
      </button>
    </div>
    <p className="bd-muted">Approving pins this core and its listed skill versions. Revoking returns the agent to its free-text background. Changes apply from the next turn.</p>
  </section>;
}
