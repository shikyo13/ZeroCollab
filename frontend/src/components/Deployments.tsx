import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage, runtime } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import './deployments.css';

type Release = { id: string; commit: string; version?: string; title?: string; subject?: string; notes?: string; created: string | number; manifest_sha: string; files: { path: string; sha256: string }[]; deletions: string[]; rehearsal?: string; rehearsal_passed?: boolean; rollback?: string; relation?: 'newer' | 'older' | 'current'; reviews?: BoardReview[] };
type BoardReview = { id: string; status: string; reviewer?: string; revision?: string; task_id?: string; task_title?: string };
type Receipt = { id: string; release_id: string; previous_release_id?: string; status: string; error?: string; steps?: unknown[]; backup?: string };
type State = { current: Partial<Release>; releases: Release[]; receipts: Receipt[]; pending: { id: string; release_id: string }[]; supervisor: { state?: string; step?: string; updated?: number; deployment_id?: string }; maintenance: { admission: string; writes: string; reason?: string } };
const labels: Record<string, string> = { deployed: 'Deployed', aborted: 'Cancelled before deployment', rolled_back: 'Previous release restored', rollback_failed: 'Recovery needed', recovery_required: 'Recovery needed', conflict: 'File conflict — recovery needed', rejected: 'Release refused', draining: 'Finishing current turns', deploying: 'Deploying', verifying: 'Checking the new release', running: 'Ready', ready: 'Ready', idle: 'Ready', restarting: 'Restarting' };
const label = (value = '') => labels[value] || value.replaceAll('_', ' ') || 'Unknown';
const reviewStates: Record<string, string> = { approved: 'Approved', changes_requested: 'Changes requested', pending: 'Waiting for review', superseded: 'Superseded', cancelled: 'Cancelled' };
const releaseName = (r?: Partial<Release>) => r?.version || r?.id || 'this release';

/** Board reviews linked to the package, shown with their live board state. */
function ReviewEvidence({ reviews }: { reviews?: BoardReview[] }) {
  if (!reviews?.length) return <>No review linked to this package.</>;
  return <>{reviews.map(r => <span key={r.id} className="deploy-review">
    {r.status === 'not_found' ? `Review ${r.id} is not on this project's board.`
      : `${reviewStates[r.status] || label(r.status)}${r.reviewer ? ` by ${r.reviewer}` : ''} for ${r.revision?.split(' ')[0] || 'this revision'} (review ${r.id}${r.task_title ? `, ${r.task_title}` : ''})`}
  </span>)}</>;
}

/** The server pushes no deployment events, so poll: quickly while the panel is open or a
 * deployment is underway, slowly otherwise (the notice still has to see a deploy start). */
const DEPLOY_POLL_ACTIVE_MS = 3000;
const DEPLOY_POLL_IDLE_MS = 30000;
const SETTLED_STATES = new Set(['idle', 'ready', 'running', '']);

function deploymentUnderway(state: State | null): boolean {
  if (!state) return false;
  return state.pending.length > 0 || state.maintenance.admission === 'closed'
    || state.maintenance.writes === 'blocked' || !SETTLED_STATES.has(state.supervisor.state ?? '');
}

export function useDeploymentState(panelOpen: boolean) {
  const [data, setData] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const next = await runtime<State>('/deployments');
      if (mounted.current) { setData(next); setError(''); setReload(false); }
    } catch (e) {
      if (mounted.current) { setError(errorMessage(e)); setReload(e instanceof ApiError && [401, 403].includes(e.status)); }
    } finally { inFlight.current = false; }
  }, []);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const delay = panelOpen || reload || deploymentUnderway(data) ? DEPLOY_POLL_ACTIVE_MS : DEPLOY_POLL_IDLE_MS;
  useEffect(() => {
    void refresh();
    let timer: number | null = null;
    const start = () => { if (timer === null && !document.hidden) timer = window.setInterval(() => void refresh(), delay); };
    const stop = () => { if (timer !== null) window.clearInterval(timer); timer = null; };
    const onVisibility = () => { if (document.hidden) stop(); else { void refresh(); start(); } };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [refresh, delay]);
  // 'settings' is the first frame of every socket (re)connect, e.g. after a restart.
  useRoomEvent('settings', () => void refresh());
  return { data, error, reload, refresh };
}
export type DeploymentFeed = ReturnType<typeof useDeploymentState>;

export function DeploymentNotice({ feed, onOpen }: { feed: DeploymentFeed; onOpen: () => void }) {
  const gate = feed.data?.maintenance;
  if (!feed.reload && gate?.admission !== 'closed' && gate?.writes !== 'blocked') return null;
  return <div className="deploy-notice" role="status">
    <span>{feed.reload ? 'The room restarted. Reload to reconnect; copy any unsent draft first.' : gate?.writes === 'blocked' ? 'Deployment in progress. The room is temporarily read-only.' : 'Deployment queued. Current turns can finish; new turns will wait.'}</span>
    {feed.reload ? <button className="bd-btn" onClick={() => window.location.reload()}>Reload room</button> : <button className="bd-btn" onClick={onOpen}>View deployment</button>}
  </div>;
}

export function Deployments({ feed }: { feed: DeploymentFeed }) {
  const { data, error, refresh } = feed;
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [approved, setApproved] = useState<{ id: string; release_id: string } | null>(null);
  // Only releases newer than the running one are offered; older ones are rollbacks and need their own action.
  const candidates = (data?.releases ?? []).filter(r => r.relation === 'newer');
  const candidate = candidates.find(r => r.id === selected) ?? candidates[0];
  const earlier = (data?.releases ?? []).filter(r => r.relation === 'older');
  const [rollbackId, setRollbackId] = useState('');
  const rollbackTarget = earlier.find(r => r.id === rollbackId) ?? earlier[0];
  // The tick confirms one exact rollback; if the target or running release changes on refresh, it no longer counts.
  const rollbackKey = rollbackTarget ? `${data?.current.id}:${rollbackTarget.id}:${rollbackTarget.manifest_sha}` : '';
  const [confirmedKey, setConfirmedKey] = useState('');
  const confirmed = !!rollbackKey && confirmedKey === rollbackKey;
  const supervisor = data?.supervisor;
  const online = !!supervisor?.updated && Date.now() / 1000 - supervisor.updated < 15;
  const ready = online && ['running', 'ready', 'idle'].includes(supervisor?.state ?? '');
  const pending = data?.pending?.[0];
  const completed = approved && data?.receipts.some(r => r.id === approved.id);
  const waiting = pending || (approved && !completed ? approved : null);
  const blocked = !ready || !!error || busy || !!waiting || data?.maintenance.admission !== 'open';
  async function approve(target: Release | undefined, rollback = false) {
    if (!target || busy || (rollback && !confirmed)) return;
    setBusy(true); setActionError('');
    try {
      const result = await runtime<{ id: string; release_id: string }>('/deployments/approve', { release_id: target.id, manifest_sha: target.manifest_sha, interrupt: false, drain_seconds: 600, rollback });
      setApproved(result); setConfirmedKey(''); await refresh();
    } catch (e) { setActionError(errorMessage(e)); await refresh(); }
    finally { setBusy(false); }
  }
  return <section className="deploy-panel bd-panel" aria-labelledby="deploy-title">
    <div className="deploy-heading"><div><h2 id="deploy-title">Deployments</h2></div><span className="bd-pill">{online ? label(supervisor?.state) : 'Supervisor offline'}</span></div>
    <p className="bd-muted">Review a release and approve it here. The supervisor handles the restart and saves the result.</p>
    {error && <p role="status" className="deploy-warning">{feed.reload ? 'The room restarted; reload to reconnect.' : 'Cannot refresh deployment status. Controls are disabled until the connection returns.'} <button className="bd-btn" onClick={() => feed.reload ? window.location.reload() : void refresh()}>{feed.reload ? 'Reload room' : 'Retry'}</button></p>}
    {!data && !error && <p role="status">Loading deployments…</p>}
    {data && <>
      <p>Running release: <code>{data.current.version || data.current.id || 'Not recorded yet'}</code></p>
      {!online && <p className="deploy-warning">The external bootstrap must start this app under the supervisor before deployments can run here.</p>}
      {candidates.length > 1 && <label className="deploy-select">Prepared release<select value={candidate?.id ?? ''} onChange={e => { setSelected(e.target.value); setActionError(''); }}>{candidates.map(r => <option key={r.id} value={r.id}>{r.title || r.version || r.id}</option>)}</select></label>}
      {candidate ? <article className="deploy-candidate">
        <h3>{candidate.title || candidate.subject || candidate.version || 'Prepared release'}</h3>
        {candidate.notes && <p className="deploy-notes">{candidate.notes}</p>}
        <p className="bd-muted">Candidate <code>{candidate.commit?.slice(0, 12)}</code> · {candidate.files?.length ?? 0} files · {candidate.deletions?.length ?? 0} removals</p>
        <div className="deploy-evidence"><p><strong>Review</strong><br /><ReviewEvidence reviews={candidate.reviews} /></p><p><strong>Rehearsal</strong><br />{candidate.rehearsal || 'No rehearsal evidence recorded in this package.'}</p><p><strong>Recovery</strong><br />{candidate.rollback || 'The supervisor must verify recovery before reopening the room.'}</p></div>
        <details><summary>Exact release and changed files</summary><dl><dt>Commit</dt><dd><code>{candidate.commit}</code></dd><dt>Package fingerprint</dt><dd><code>{candidate.manifest_sha}</code></dd></dl><ul className="deploy-files">{candidate.files?.map(f => <li key={f.path}>{f.path}</li>)}{candidate.deletions?.map(p => <li key={p}>Remove: {p}</li>)}</ul></details>
        <p className="deploy-warning">Approval waits up to 10 minutes for current turns to finish, then briefly takes the room offline. If turns cannot finish in time, deployment is cancelled.</p>
        <button className="bd-btn bd-btn-primary" disabled={blocked || !candidate.rehearsal_passed} onClick={() => void approve(candidate)}>{busy ? 'Submitting approval…' : 'Approve deployment when idle'}</button>
        <p className="bd-muted">Applies only to this exact release. Current turns will not be forcibly interrupted.</p>
      </article> : <p className="bd-muted">No newer release is staged. The team prepares and reviews a package before you can approve it.</p>}
      {rollbackTarget && <details className="deploy-rollback">
        <summary>Earlier releases (rollback) · {earlier.length}</summary>
        {earlier.length > 1 && <label className="deploy-select">Earlier release<select value={rollbackTarget.id} onChange={e => { setRollbackId(e.target.value); setConfirmedKey(''); setActionError(''); }}>{earlier.map(r => <option key={r.id} value={r.id}>{r.title || r.version || r.id}</option>)}</select></label>}
        <p><strong>{rollbackTarget.title || rollbackTarget.subject || releaseName(rollbackTarget)}</strong><br /><span className="bd-muted">Built {String(rollbackTarget.created)} · <code>{rollbackTarget.commit?.slice(0, 12)}</code> · Review: <ReviewEvidence reviews={rollbackTarget.reviews} /></span></p>
        <p className="deploy-warning">Rolls the room back from {releaseName(data.current)} to {releaseName(rollbackTarget)}. Anything added since {releaseName(rollbackTarget)} disappears, and settings saved by newer releases may not load.</p>
        <label className="deploy-confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmedKey(e.target.checked ? rollbackKey : '')} /> I understand this is a rollback to an older release</label>
        <button className="bd-btn deploy-rollback-btn" disabled={blocked || !confirmed || !rollbackTarget.rehearsal_passed} onClick={() => void approve(rollbackTarget, true)}>{busy ? 'Submitting…' : `Roll back to ${releaseName(rollbackTarget)} when idle`}</button>
      </details>}
      {actionError && <p role="alert" className="bd-error">{actionError}</p>}
      {waiting && <p role="status" className="deploy-warning">Approval recorded for <code>{waiting.release_id}</code>. {label(supervisor?.step || supervisor?.state)}. You can leave this page; the supervisor saves the result.</p>}
      <h3>Recent results</h3>
      {!data.receipts?.length && <p className="bd-muted">No deployment receipts yet.</p>}
      {data.receipts?.map(r => <details className="deploy-receipt" key={r.id}><summary><strong>{label(r.status)}</strong> · <code>{r.release_id}</code></summary>{r.error && <p role="status" className="bd-error">{r.error}</p>}<p>Previous release: <code>{r.previous_release_id || 'Not recorded'}</code></p><pre>{JSON.stringify(r, null, 2)}</pre></details>)}
    </>}
  </section>;
}
