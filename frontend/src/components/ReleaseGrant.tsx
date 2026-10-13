import { useStoreValue } from '../lib/room';
import { useReleaseGrant } from './useReleaseGrant';
import './releaseGrant.css';

export function ReleaseGrantControl() {
  const project = useStoreValue('projectId', window.activeProjectId || '');
  const { view, busy, error, note, run } = useReleaseGrant(project);
  const available = !!view && !busy && !!project;
  const date = view?.changed_at && Number.isFinite(view.changed_at) ? new Date(view.changed_at * 1000) : null;
  const changed = date && Number.isFinite(date.getTime()) ? date : null;
  return <section className="release-grant" aria-labelledby="release-grant-title">
    <div className="release-grant-heading"><h3 id="release-grant-title">Automatic room releases</h3>
      <span className="bd-pill">{view ? view.enabled ? 'Enabled' : 'Off' : busy ? 'Checking...' : 'Unavailable'}</span></div>
    <p>Allow <strong>Astra</strong> to install reviewed room releases for this project after the previous release's checks pass.</p>
    <p className="bd-muted">Only you can enable or revoke this permission. Stop still prevents automatic deployment. Host supervisor updates require separate approval.</p>
    <p className="bd-muted">Deployments wait while any connected tab has a draft or an active editor. Drafts stay in the tab through maintenance and reconnect.</p>
    {view?.lapse_reason && <p className="deploy-warning">Permission lapsed: {view.lapse_reason} Review it before enabling again.</p>}
    <div className="release-grant-actions">
      <button type="button" className={`bd-btn${view?.enabled ? '' : ' bd-btn-primary'}`} disabled={!available}
        data-release-control={view?.enabled ? 'revoke' : undefined}
        onClick={() => available && void run(!view!.enabled)}>{busy ? 'Checking permission...' : view?.enabled ? 'Revoke permission' : 'Enable for Astra'}</button>
      <button type="button" className="bd-btn" disabled={busy || !project} onClick={() => void run()}>Refresh permission</button>
    </div>
    <p className="release-grant-meta">{project ? <>Project: <code>{project}</code></> : 'Select a project to manage its release permission.'}
      {changed && <> · Changed <time dateTime={changed.toISOString()}>{changed.toLocaleString()}</time></>}</p>
    {error && <p className="deploy-warning" role="alert">{error}</p>}
    {note && <p className="deploy-warning" role="status">{note}</p>}
  </section>;
}
