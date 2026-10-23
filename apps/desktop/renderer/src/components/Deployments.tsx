import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage, runtime, sessionToken } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import './deployments.css'
import { ReleaseGrantControl } from './ReleaseGrant';

type Release = { id: string; commit: string; version?: string; title?: string; subject?: string; notes?: string; installed_at?: string; created: string | number; manifest_sha: string; files: { path: string; sha256: string }[]; deletions: string[]; rehearsal?: string; rehearsal_passed?: boolean; rollback?: string; relation?: 'newer' | 'older' | 'current'; reviews?: BoardReview[] };
type BoardReview = { id: string; status: string; reviewer?: string; revision?: string; task_id?: string; task_title?: string };
// The supervisor writes the outcome sentence (including a refusal's reason) to detail.
type Receipt = { id: string; release_id: string; previous_release_id?: string; status: string; detail?: string; error?: string; started?: string; steps?: { time?: string; step?: string }[]; backup?: string };
type PendingInstall = { id: string; release_id: string; interrupt?: boolean; drain_seconds?: number };
type State = { current: Partial<Release>; releases: Release[]; receipts: Receipt[]; pending: PendingInstall[]; supervisor: { state?: string; step?: string; updated?: number; deployment_id?: string; agent_install_interrupt?: number }; maintenance: { admission: string; writes: string; reason?: string; interrupt?: boolean } };
const labels: Record<string, string> = { deployed: 'Deployed', aborted: 'Cancelled before deployment', rolled_back: 'Previous release restored', rollback_failed: 'Recovery needed', recovery_required: 'Recovery needed', conflict: 'File conflict, recovery needed', rejected: 'Release refused', draining: 'Finishing current turns', deploying: 'Deploying', verifying: 'Checking the new release', running: 'Ready', ready: 'Ready', idle: 'Ready', restarting: 'Restarting' };
const label = (value = '') => labels[value] || value.replaceAll('_', ' ') || 'Unknown';
const reviewStates: Record<string, string> = { approved: 'Approved', changes_requested: 'Changes requested', pending: 'Waiting for review', superseded: 'Superseded', cancelled: 'Cancelled' };
function deploymentPhase(state: State | null): string {
  if (state?.maintenance.writes === 'blocked') return label(state.supervisor.step || state.supervisor.state);
  if (state?.maintenance.admission === 'closed') return state.maintenance.interrupt === true ? 'Interrupting current turns' : 'Finishing current turns';
  return label(state?.supervisor.step || state?.supervisor.state);
}
const releaseName = (r?: Partial<Release>) => r?.version || r?.id || 'this release';

type NoteSection = { heading: string; text: string };
/** Chat's heading/list/bold/code subset, rendered as React text nodes. No HTML,
 * links or classic-script marked dependency enters this release surface. */
function noteInline(text: string) {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') ? <code key={i}>{part.slice(1, -1)}</code>
      : part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2).split(/(`[^`\n]+`)/g).map((s, j) => s.startsWith('`') && s.endsWith('`') ? <code key={j}>{s.slice(1, -1)}</code> : s)}</strong>
        : part);
}
function splitNotes(notes = ''): NoteSection[] | null {
  const lines = notes.replace(/\r\n?/g, '\n').split('\n');
  if (!lines.some(line => /^##\s+\S/.test(line))) return null;
  const sections: NoteSection[] = [];
  let section: NoteSection = { heading: '', text: '' };
  for (const line of lines) {
    const heading = /^##\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading) {
      if (section.text.trim()) sections.push(section);
      section = { heading: heading[1], text: '' };
    } else section.text += line + '\n';
  }
  if (section.text.trim()) sections.push(section);
  return sections;
}
function NoteBody({ text }: { text: string }) {
  const blocks: { list: boolean; lines: string[] }[] = [];
  for (const line of text.trim().split('\n')) {
    if (!line.trim()) { blocks.push({ list: false, lines: [] }); continue; }
    const bullet = /^\s*[-*+]\s+(.+)$/.exec(line);
    let block = blocks.at(-1);
    if (!block || block.list !== !!bullet) { block = { list: !!bullet, lines: [] }; blocks.push(block); }
    block.lines.push(bullet ? bullet[1] : line);
  }
  return <>{blocks.filter(b => b.lines.length).map((b, i) => b.list
    ? <ul key={i}>{b.lines.map((line, j) => <li key={j}>{noteInline(line)}</li>)}</ul>
    : <p key={i}>{noteInline(b.lines.join('\n'))}</p>)}</>;
}
function ReleaseNotes({ notes, technical = false }: { notes?: string; technical?: boolean }) {
  const sections = splitNotes(notes);
  if (!sections) return technical ? null : <p className="deploy-notes">{notes?.trim() ? notes : 'No release notes were provided for this package.'}</p>;
  return <>{sections.filter(s => (s.heading.toLowerCase() === 'technical') === technical).map((s, i) =>
    <section key={i} className={`deploy-note-section${s.heading.toLowerCase() === 'needs you' ? ' deploy-needs-you' : ''}`}>
      {s.heading && <h4>{s.heading}</h4>}<div><NoteBody text={s.text} /></div>
    </section>)}</>;
}
function Changelog({ data }: { data: State }) {
  // installed_at comes from the server's complete successful receipt history.
  // "older" describes staging order, not proof that a package was installed.
  const current = data.releases.find(r => r.id === data.current.id)
    ?? (data.current.id ? data.current : undefined);
  const past = data.releases.filter(r => r.id !== data.current.id && !!r.installed_at)
    .sort((a, b) => b.installed_at!.localeCompare(a.installed_at!));
  const ordered = current ? [current, ...past] : past;
  return <section className="deploy-changelog" aria-labelledby="changelog-title">
    <h3 id="changelog-title">Changelog</h3>
    <p className="bd-muted">Release notes for the current and previously installed releases.</p>
    {!ordered.length && <p className="bd-muted">No release notes are available yet.</p>}
    {ordered.map(r => {
      // Server-local YYYY-MM-DD HH:MM:SS; never substitute a build date.
      const installed = r.installed_at;
      const isCurrent = r.id === data.current.id;
      return <details className="deploy-release-entry" key={`${r.id}:${isCurrent}`} open={isCurrent}>
        <summary><span className="deploy-release-summary"><span><strong>{releaseName(r)}</strong>{isCurrent && <span className="bd-pill">Current</span>}</span>
          <span className="deploy-installed-date bd-muted">{installed ? <>Installed <time>{installed}</time></> : 'Install date not recorded'}</span></span></summary>
        <div className="deploy-release-body">
          <h4 className="deploy-release-title">{r.title || r.subject || releaseName(r)}</h4>
          <ReleaseNotes notes={r.notes} />
          {splitNotes(r.notes)?.some(s => s.heading.toLowerCase() === 'technical') && <details className="deploy-technical"><summary>Technical details</summary><ReleaseNotes notes={r.notes} technical /></details>}
        </div>
      </details>;
    })}
  </section>;
}

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
const SUPERVISOR_FRESH_MS = 15000;
function heartbeatFresh(updated?: number, now = Date.now()): boolean {
  return !!updated && Number.isFinite(updated) && updated * 1000 <= now
    && now - updated * 1000 < SUPERVISOR_FRESH_MS;
}

function deploymentUnderway(state: State | null): boolean {
  if (!state) return false;
  return state.pending.length > 0 || state.maintenance.admission === 'closed'
    || state.maintenance.writes === 'blocked' || !SETTLED_STATES.has(state.supervisor.state ?? '');
}

export function useDeploymentState(panelOpen: boolean) {
  const [data, setData] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(false);
  const [now, setNow] = useState(Date.now);
  const updated = data?.supervisor.updated;
  useEffect(() => {
    setNow(Date.now());
    const remaining = (updated ?? 0) * 1000 + SUPERVISOR_FRESH_MS - Date.now();
    if (!updated || !Number.isFinite(updated) || updated * 1000 > Date.now() || remaining <= 0) return;
    // Expire approval even between polls, or while the next request is stalled.
    const timer = window.setTimeout(() => setNow(Date.now()), remaining + 1);
    return () => window.clearTimeout(timer);
  }, [updated]);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const authBlocked = useRef(false);
  const [signedOut, setSignedOut] = useState(false);
  const stopForSignOut = useCallback(() => {
    authBlocked.current = true;
    setSignedOut(true); setData(null); setError(''); setReload(false);
  }, []);
  const refresh = useCallback(async () => {
    if (inFlight.current || authBlocked.current) return;
    inFlight.current = true;
    try {
      const next = await runtime<State>('/deployments');
      if (mounted.current && !authBlocked.current) { setData(next); setError(''); setReload(false); }
    } catch (e) {
      if (mounted.current && !authBlocked.current) {
        const rejected = e instanceof ApiError && [401, 403].includes(e.status);
        // Cookie sessions survive restarts. Rejection means sign-in is required,
        // not that reloading can restore the connection.
        if (rejected && !sessionToken()) stopForSignOut();
        else { setError(errorMessage(e)); setReload(rejected); }
      }
    } finally { inFlight.current = false; }
  }, [stopForSignOut]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const delay = panelOpen || reload || deploymentUnderway(data) ? DEPLOY_POLL_ACTIVE_MS : DEPLOY_POLL_IDLE_MS;
  useEffect(() => {
    if (signedOut) return;
    void refresh();
    let timer: number | null = null;
    const start = () => { if (timer === null && !document.hidden) timer = window.setInterval(() => void refresh(), delay); };
    const stop = () => { if (timer !== null) window.clearInterval(timer); timer = null; };
    const onVisibility = () => { if (document.hidden) stop(); else { void refresh(); start(); } };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [refresh, delay, signedOut]);
  useRoomEvent('chat_connection', event => {
    if ((event as unknown as { state?: string }).state === 'signed_out' && !sessionToken()) stopForSignOut();
  });
  useEffect(() => {
    if ((window as Window & { roomConnectionState?: string }).roomConnectionState === 'signed_out' && !sessionToken()) stopForSignOut();
  }, [stopForSignOut]);
  // 'settings' is the first frame of every socket (re)connect, e.g. after a restart.
  useRoomEvent('settings', () => void refresh());
  const fresh = heartbeatFresh(updated, Math.max(now, Date.now()));
  const ready = !signedOut && !error && fresh && ['running', 'ready', 'idle'].includes(data?.supervisor.state ?? '');
  return { data, error, reload, refresh, fresh, ready };
}
export type DeploymentFeed = ReturnType<typeof useDeploymentState>;

export function SupervisorStatus({ feed }: { feed: DeploymentFeed }) {
  const updated = feed.data?.supervisor.updated;
  const validTime = !!updated && Number.isFinite(updated) && updated > 0 && updated * 1000 <= Date.now();
  return <div>
    <p role="status">{!feed.data && !feed.error ? 'Checking deployment supervisor…'
      : feed.error || !feed.fresh ? 'Deployment supervisor unavailable'
      : `Deployment supervisor: ${deploymentPhase(feed.data)}`}</p>
    <p className="bd-muted">{validTime ? <>Last confirmation: <time dateTime={new Date(updated * 1000).toISOString()}>{new Date(updated * 1000).toLocaleString()}</time>.</>
      : 'No supervisor heartbeat received.'} Approval requires a heartbeat less than 15 seconds old.</p>
    {(feed.error || (feed.data && !feed.fresh)) && <p className="deploy-warning">Deployment status could not be confirmed. The backend can keep running while updates are unavailable. <button className="bd-btn" onClick={() => void feed.refresh()}>Refresh status</button></p>}
  </div>;
}

export function DeploymentNotice({ feed, onOpen }: { feed: DeploymentFeed; onOpen: () => void }) {
  const gate = feed.data?.maintenance;
  if (!feed.reload && gate?.admission !== 'closed' && gate?.writes !== 'blocked') return null;
  return <div className="deploy-notice" role="status">
    <span>{feed.reload ? 'The room restarted. Reload to reconnect; copy any unsent draft first.' : gate?.writes === 'blocked' ? 'Deployment in progress. The room is temporarily read-only.' : gate?.interrupt === true ? 'Interrupting current turns for the install. Queued work is kept.' : 'Deployment queued. Current turns can finish; new turns will wait.'}</span>
    {feed.reload ? <button className="bd-btn" onClick={() => window.location.reload()}>Reload room</button> : <button className="bd-btn" onClick={onOpen}>View deployment</button>}
  </div>;
}

export function Deployments({ feed }: { feed: DeploymentFeed }) {
  const { data, error, refresh } = feed;
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [approved, setApproved] = useState<PendingInstall | null>(null);
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
  const pending = data?.pending?.[0];
  const completed = approved && data?.receipts.some(r => r.id === approved.id);
  const waiting = pending || (approved && !completed ? approved : null);
  const blockingReason = !data ? 'Deployment status is loading.'
    : error ? 'Deployment status could not be refreshed. Refresh status before approving.'
    : !feed.fresh ? 'The deployment supervisor has no recent heartbeat. Refresh status; approval stays blocked until it checks in.'
    : !feed.ready ? `The deployment supervisor is ${deploymentPhase(data).toLowerCase()}. Wait until it is ready.`
    : busy ? 'Approval is being submitted.'
    : waiting ? 'A deployment approval is already queued. Wait for its result.'
    : data.maintenance.admission !== 'open' || data.maintenance.writes === 'blocked' ? data.maintenance.reason || 'The room is temporarily closed for deployment.'
    : '';
  const candidateReason = blockingReason || (candidate && !candidate.rehearsal_passed ? 'This exact release has not passed rehearsal.' : '');
  const rollbackReason = blockingReason || (!confirmed ? 'Confirm that you understand the rollback before approving.' : !rollbackTarget?.rehearsal_passed ? 'This exact release has not passed rehearsal.' : '');
  async function approve(target: Release | undefined, rollback = false, mode: 'idle' | 'now' = 'idle') {
    if (!target || blockingReason || !heartbeatFresh(supervisor?.updated) || !target.rehearsal_passed || (rollback && !confirmed)) return;
    setBusy(true); setActionError('');
    try {
      const interrupt = !rollback && mode === 'now';
      const result = await runtime<PendingInstall>('/deployments/approve', { release_id: target.id, manifest_sha: target.manifest_sha, interrupt, drain_seconds: interrupt ? 30 : 600, rollback });
      setApproved(result); setConfirmedKey(''); await refresh();
    } catch (e) { setActionError(errorMessage(e)); await refresh(); }
    finally { setBusy(false); }
  }
  return <section className="deploy-panel bd-panel" aria-labelledby="deploy-title">
    <div className="deploy-heading"><div><h2 id="deploy-title">Updates</h2></div></div>
    <p className="bd-muted">Review a release and approve it here. The supervisor handles the restart and saves the result.</p>
    <SupervisorStatus feed={feed} />
    <ReleaseGrantControl interruptsTurns={data && feed.fresh && !error ? supervisor?.agent_install_interrupt === 1 : undefined} />
    {error && <p role="status" className="deploy-warning">{feed.reload ? 'The room restarted; reload to reconnect.' : 'Cannot refresh deployment status. Controls are disabled until the connection returns.'} <button className="bd-btn" onClick={() => feed.reload ? window.location.reload() : void refresh()}>{feed.reload ? 'Reload room' : 'Retry'}</button></p>}
    {!data && !error && <p role="status">Loading deployments…</p>}
    {data && <>
      <p>Running release: <code>{data.current.version || data.current.id || 'Not recorded yet'}</code></p>
      {candidates.length > 1 && <label className="deploy-select">Prepared release<select value={candidate?.id ?? ''} onChange={e => { setSelected(e.target.value); setActionError(''); }}>{candidates.map(r => <option key={r.id} value={r.id}>{r.title || r.version || r.id}</option>)}</select></label>}
      {candidate ? <article className="deploy-candidate">
        <p className="bd-muted">Release {releaseName(candidate)}</p>
        <h3>{candidate.title || candidate.subject || candidate.version || 'Prepared release'}</h3>
        <section className="deploy-changes" aria-label="Release notes">
          <ReleaseNotes notes={candidate.notes} />
        </section>
        <details key={candidate.id} className="deploy-technical">
          <summary>Technical details: review, rehearsal and recovery</summary>
          <ReleaseNotes notes={candidate.notes} technical />
          <p className="bd-muted">Candidate <code>{candidate.commit?.slice(0, 12)}</code> · {candidate.files?.length ?? 0} files · {candidate.deletions?.length ?? 0} removals</p>
          <div className="deploy-evidence"><p><strong>Review</strong><br /><ReviewEvidence reviews={candidate.reviews} /></p><p><strong>Rehearsal</strong><br />{candidate.rehearsal || 'No rehearsal evidence recorded in this package.'}</p><p><strong>Recovery</strong><br />{candidate.rollback || 'The supervisor must verify recovery before reopening the room.'}</p></div>
          <h4>Exact release and changed files</h4>
          <dl><dt>Commit</dt><dd><code>{candidate.commit}</code></dd><dt>Package fingerprint</dt><dd><code>{candidate.manifest_sha}</code></dd></dl>
          <ul className="deploy-files">{candidate.files?.map(f => <li key={f.path}>{f.path}</li>)}{candidate.deletions?.map(p => <li key={p}>Remove: {p}</li>)}</ul>
        </details>
        {candidateReason && <p id="deployment-blocked-reason" className="deploy-warning" role="status" tabIndex={0}><strong>Approval unavailable:</strong> {candidateReason}</p>}
        <div className="deploy-install-actions">
          <button className="bd-btn bd-btn-primary" aria-describedby={`deployment-now-help${candidateReason ? ' deployment-blocked-reason' : ''}`} disabled={!!candidateReason} onClick={() => void approve(candidate, false, 'now')}>{busy ? 'Submitting approval…' : 'Install now'}</button>
          <button className="bd-btn" aria-describedby={`deployment-idle-help${candidateReason ? ' deployment-blocked-reason' : ''}`} disabled={!!candidateReason} onClick={() => void approve(candidate)}>{busy ? 'Submitting approval…' : 'Approve when idle'}</button>
        </div>
        <p id="deployment-now-help"><strong>Install now:</strong> Running turns get 30 seconds to finish, then are interrupted. Queued work is kept; interrupted work is scheduled to continue after the install.</p>
        <p id="deployment-idle-help" className="bd-muted"><strong>Approve when idle:</strong> Waits up to 10 minutes without interrupting current turns. If they cannot finish in time, the install is cancelled.</p>
        <p className="bd-muted">Both choices apply only to this exact release. The room briefly goes offline during the install; unsent drafts stay in their tabs.</p>
      </article> : <p className="bd-muted">No newer release is staged. The team prepares and reviews a package before you can approve it.</p>}
      <Changelog data={data} />
      {rollbackTarget && <details className="deploy-rollback">
        <summary>Earlier releases (rollback) · {earlier.length}</summary>
        {earlier.length > 1 && <label className="deploy-select">Earlier release<select value={rollbackTarget.id} onChange={e => { setRollbackId(e.target.value); setConfirmedKey(''); setActionError(''); }}>{earlier.map(r => <option key={r.id} value={r.id}>{r.title || r.version || r.id}</option>)}</select></label>}
        <p><strong>{rollbackTarget.title || rollbackTarget.subject || releaseName(rollbackTarget)}</strong><br /><span className="bd-muted">Built {String(rollbackTarget.created)} · <code>{rollbackTarget.commit?.slice(0, 12)}</code> · Review: <ReviewEvidence reviews={rollbackTarget.reviews} /></span></p>
        <p className="deploy-warning">Rolls the room back from {releaseName(data.current)} to {releaseName(rollbackTarget)}. Anything added since {releaseName(rollbackTarget)} disappears, and settings saved by newer releases may not load.</p>
        <label className="deploy-confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmedKey(e.target.checked ? rollbackKey : '')} /> I understand this is a rollback to an older release</label>
        {rollbackReason && <p id="rollback-blocked-reason" className="deploy-warning" role="status" tabIndex={0}><strong>Rollback unavailable:</strong> {rollbackReason}</p>}
        <button className="bd-btn deploy-rollback-btn" aria-describedby={rollbackReason ? 'rollback-blocked-reason' : undefined} disabled={!!rollbackReason} onClick={() => void approve(rollbackTarget, true)}>{busy ? 'Submitting…' : `Roll back to ${releaseName(rollbackTarget)} when idle`}</button>
      </details>}
      {actionError && <p role="alert" className="bd-error">{actionError}</p>}
      {(waiting || data.maintenance.admission === 'closed' || data.maintenance.writes === 'blocked') && <p role="status" className="deploy-warning">{waiting ? <>Approval recorded for <code>{waiting.release_id}</code>. </> : 'Install in progress. '}{waiting && data.maintenance.admission === 'open' && data.maintenance.writes !== 'blocked' && SETTLED_STATES.has(supervisor?.state ?? '') ? 'Waiting for the supervisor' : deploymentPhase(data)}. {waiting?.interrupt === true && data.maintenance.admission !== 'closed' && data.maintenance.writes !== 'blocked' && 'Running turns get up to 30 seconds before interruption. '}You can leave this page; the supervisor saves the result.</p>}

    </>}
  </section>;
}

export function DeploymentHistory({ feed }: { feed: DeploymentFeed }) {
  const data = feed.data;
  // Receipts name releases by id; show the version when the release is still known.
  const versions = new Map([...(data?.releases ?? []), data?.current ?? {}].map(r => [r.id, r.version]));
  const receiptRelease = (id: string) => versions.get(id) ? <>{versions.get(id)} <code className="bd-muted">{id}</code></> : <code>{id}</code>;
  return <section className="deploy-panel bd-panel" aria-labelledby="deployment-history-title">
    <h2 id="deployment-history-title">Update history</h2>
    {feed.error && <p role="status" className="deploy-warning">History could not be refreshed. <button className="bd-btn" onClick={() => void feed.refresh()}>Retry</button></p>}
    {!data && !feed.error && <p role="status">Loading history...</p>}
    {data && <>
      {!data.receipts?.length && <p className="bd-muted">No deployment receipts yet.</p>}
      {!!data.receipts?.length && <div className="deploy-receipts">{data.receipts.map(r => {
        const reason = r.error || r.detail;
        const failed = r.status !== 'deployed';
        return <details className="deploy-receipt" key={r.id}>
          <summary>
            <span className="deploy-receipt-line"><strong className={failed ? 'deploy-receipt-failed' : undefined}>{label(r.status)}</strong> · {receiptRelease(r.release_id)}{r.started && <span className="bd-muted"> · {r.started}</span>}</span>
            {failed && reason && <span className="deploy-receipt-reason">{reason}</span>}
          </summary>
          {!failed && reason && <p>{reason}</p>}
          <p>Previous release: {r.previous_release_id ? receiptRelease(r.previous_release_id) : 'Not recorded'}</p>
          {failed && r.backup && <p>Backup: <code>{r.backup}</code></p>}
          {!!r.steps?.length && <ol className="deploy-receipt-steps">{r.steps.map((s, i) => <li key={i}><span className="bd-muted">{s.time}</span> {s.step}</li>)}</ol>}
        </details>;
      })}</div>}
    </>}
  </section>;
}
