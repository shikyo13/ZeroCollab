import { useCallback, useEffect, useRef, useState } from 'react';
import './backend-controls.css';

type Action = 'Start' | 'Stop' | 'Restart' | 'Install';
type Update = { id: string; version?: string; manifest_sha: string };
type Status = { state: string; alive: boolean; busy: boolean; action: string; message: string; token: string;
  controller?: { connected: boolean; version: string; in_sync: boolean; refresh_error?: string }; current?: { version?: string };
  update?: Update | null; progress?: string; receipt?: { status: string; detail: string } | null };
const controller = 'http://127.0.0.1:8301';

/** The tray's local controller outlives the backend, so this mounted UI can
 * still start it after Stop. It never relies on the backend's API/session. */
export function BackendControls({ inline = false }: { inline?: boolean }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [pending, setPending] = useState<Action | null>(null);
  const [confirmation, setConfirmation] = useState<Action | null>(null);
  const [installTarget, setInstallTarget] = useState<Update | null>(null);
  const [error, setError] = useState('');
  const [reconnect, setReconnect] = useState(false);
  const mounted = useRef(true);
  const checking = useRef(false);
  const actionInFlight = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    if (checking.current) return;
    checking.current = true;
    try {
      const response = await fetch(`${controller}/status`, { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(12000) });
      if (!response.ok) throw new Error('Controller unavailable');
      const next: Status = await response.json();
      if (mounted.current) { setStatus(next); setUnavailable(false); }
    } catch {
      if (mounted.current) { setStatus(null); setUnavailable(true); }
    } finally { checking.current = false; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, inline || open || pending || status?.busy ? 2500 : 15000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { mounted.current = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [inline, open, pending, status?.busy, refresh]);

  useEffect(() => {
    if (!open || inline) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); setConfirmation(null); trigger.current?.focus(); }
    };
    const outside = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) { setOpen(false); setConfirmation(null); }
    };
    document.addEventListener('keydown', close);
    document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', close); document.removeEventListener('pointerdown', outside); };
  }, [open, inline]);

  async function run(action: Action) {
    if (!status || status.busy || actionInFlight.current) return;
    if (action === 'Install' && !installTarget) return;
    actionInFlight.current = true;
    setConfirmation(null); setPending(action); setError('');
    try {
      const response = await fetch(`${controller}/action`, {
        method: 'POST', credentials: 'omit', signal: AbortSignal.timeout(15000),
        headers: { 'Content-Type': 'application/json', 'X-Control-Token': status.token },
        body: JSON.stringify({ action, ...(action === 'Install' && installTarget
          ? { release_id: installTarget.id, manifest_sha: installTarget.manifest_sha } : {}) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `${action} was not accepted.`);
      if (action !== 'Stop') setReconnect(true);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the controller. Check status before retrying.');
    } finally { setPending(null); actionInFlight.current = false; }
  }

  const busy = !!pending || !!status?.busy;
  const label = unavailable ? 'Tray controller not connected' : !status ? 'Checking tray controller…'
    : busy ? status.progress || `Backend: ${pending || status.action || 'update'}…` : `Backend ${status.state.replaceAll('_', ' ')}`;
  return <div className="backend-controls">
    {!inline && <button ref={trigger} className="ws-nav-item backend-trigger" title={`Backend controls: ${label}`} aria-label={`Backend controls: ${label}`} aria-expanded={open} aria-controls="backend-panel" onClick={() => { setOpen(!open); setConfirmation(null); void refresh(); }}>
      <i className="backend-indicator" aria-hidden="true"><i className={`backend-dot ${status?.alive ? 'is-running' : ''}`} /></i>
      <span>Backend</span><small className="backend-status">{label}</small>
    </button>}
    {(inline || open) && <div ref={panel} id="backend-panel" className={`backend-panel${inline ? ' backend-inline' : ''}`} role="region" aria-label="Backend controls">
      <div className="backend-heading"><strong>Backend controls</strong>{!inline && <button aria-label="Close backend controls" onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button>}</div>
      <p role="status">{label}</p>
      {unavailable ? <p>Start <strong>ZeroCollab Control</strong> using its desktop shortcut. If it is already in the notification area, open it and check its connection. These controls need the tray controller, even while the room is stopped.</p>
        : status && <p className="backend-controller-state">Tray controller connected · {status.controller?.version || 'Older controller'}
          {status.controller ? status.controller.in_sync ? ' · In sync' : status.controller.refresh_error ? ' · Refresh failed; open controller logs' : ' · Refresh pending' : ' · Update the controller copy'}
          {status.current?.version && <><br />Installed room: {status.current.version}</>}</p>}
      {!unavailable && <p>Stop interrupts active agent turns. Saved chats and files remain.</p>}
      <div className="backend-actions">
        <button disabled={!status || busy || status.alive} onClick={() => void run('Start')}>Start</button>
        <button disabled={!status || busy || !status.alive} onClick={() => setConfirmation('Stop')}>Stop</button>
        <button disabled={!status || busy || !status.alive} onClick={() => setConfirmation('Restart')}>Restart</button>
      </div>
      {status?.update && <div className="backend-update"><p><strong>{status.update.version || status.update.id} ready</strong><br />Uses the same approved package as Server Updates.</p>
        <button disabled={busy} onClick={() => { setInstallTarget(status.update ?? null); setConfirmation('Install'); }}>Install update</button></div>}
      {confirmation && <div className="backend-confirm" role="alert">
        <p>{confirmation === 'Install'
          ? `Install ${installTarget?.version || installTarget?.id}? Current turns can finish before the room restarts. A stopped room will be started to install. Copy any unsent draft first.`
          : `${confirmation} the backend now? Active turns will be interrupted. Copy any unsent draft first.`}</p>
        <div className="backend-actions"><button disabled={busy} onClick={() => void run(confirmation)}>Confirm {confirmation.toLowerCase()}</button><button onClick={() => setConfirmation(null)}>Cancel</button></div>
      </div>}
      {error && <p className="backend-error" role="alert">{error}</p>}
      {status?.message && <p>{status.message}</p>}
      {status?.receipt && !busy && <p>Latest update: {status.receipt.status.replaceAll('_', ' ')}. {status.receipt.detail}</p>}
      {reconnect && status?.state === 'running' && !busy && <div className="backend-confirm"><p>Backend is ready. Copy any unsent draft before reloading to reconnect.</p><button onClick={() => window.location.reload()}>Reload ZeroCollab</button></div>}
      {!status?.alive && !unavailable && <p>Keep this tab open to start again here. If you close it, use the tray controller.</p>}
      {unavailable && <button onClick={() => void refresh()}>Retry connection</button>}
    </div>}
  </div>;
}
