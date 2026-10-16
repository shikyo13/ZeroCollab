/* One saved Project manager identity. The server chooses its coordination task,
 * chat and project scope; the Team form saves identity and automatic checks together. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { runtime, errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import { leaderName } from '../lib/responders';
import type { Responders, RuntimeState } from '../lib/types';
import { ErrorText, Field, RelativeTime, useAction } from './shared';

export interface ManagerAuditStatus {
  project_id: string;
  enabled: boolean;
  generation: string | null;
  config: { manager: string; task_id: string; channel: string; interval_minutes: number } | null;
  selected_manager: { id: string | null; label: string; eligible: boolean; reason_code: string; reason_text: string };
  task: { id: string; title: string } | null;
  channel: { id: string; title: string } | null;
  scope: { mode: 'project' | 'folders'; folders: string[]; excluded: string[] };
  options: {
    managers: { id: string; label: string; eligible: boolean; reason?: string }[];
    interval: { min: number; max: number; default: number };
  };
  budget: { total: number; used: number; remaining: number; per_hour: number; shared_per_hour: number } | null;
  next_due: number | null;
  last: { time: number; outcome: string; detail?: string } | null;
  disabled_reason: string | null;
}

const SAVED_EVENT = 'project-manager-saved';
const OUTCOMES: Record<string, string> = {
  audited: 'Checked in', no_findings: 'Nothing needed attention; no agent turn started',
  covered: 'Skipped: the manager was already working', refused: 'Could not check in', failed: 'Did not finish',
};

function useManagerStatus(project: string) {
  const [status, setStatus] = useState<ManagerAuditStatus | null>(null);
  const [error, setError] = useState('');
  const lastRead = useRef(0);
  const sequence = useRef(0);
  const scope = useRef(project);
  scope.current = project;
  const load = useCallback(async () => {
    const request = ++sequence.current;
    lastRead.current = Date.now();
    try {
      const next = await runtime<ManagerAuditStatus>('/manager-audit');
      if (scope.current !== project || request !== sequence.current) return;
      if (project && next.project_id !== project) throw new Error('The open project changed. Reload its manager settings.');
      setStatus(next); setError('');
    } catch (e) { if (scope.current === project && request === sequence.current) setError(errorMessage(e)); }
  }, [project]);
  useEffect(() => { setStatus(null); void load(); }, [load]);
  useEffect(() => {
    const read = () => void load();
    window.addEventListener(SAVED_EVENT, read);
    return () => window.removeEventListener(SAVED_EVENT, read);
  }, [load]);
  // Runtime snapshots can be frequent. Refresh this small status at most twice a minute.
  useRoomEvent('runtime_state', () => { if (Date.now() - lastRead.current >= 30000) void load(); });
  const apply = useCallback((next: ManagerAuditStatus) => { ++sequence.current; setStatus(next); setError(''); }, []);
  return { status, setStatus: apply, error, load };
}

function openTeam() {
  const url = new URL(location.href); url.searchParams.set('view', 'team');
  history.pushState({}, '', url); window.dispatchEvent(new PopStateEvent('popstate'));
}

/** Team owns the single editable setup. A refused Save keeps both saved identity and draft. */
export function ProjectManagerControls({ state, refresh }: { state: RuntimeState | null; refresh: () => Promise<unknown> }) {
  const project = state?.project_id ?? state?.project ?? '';
  const { status, setStatus, error: loadError, load } = useManagerStatus(project);
  const [saved, setSaved] = useState(state?.responders);
  const [draft, setDraft] = useState({ lead: '', enabled: false, interval: 30 });
  const [edited, setEdited] = useState(false);
  const { run, busy, error, setError } = useAction();
  const submitted = useRef(false);
  const chosen = state?.responders?.configured.lead ?? '';
  useEffect(() => { if (state?.responders) setSaved(state.responders); }, [state?.responders]);
  useEffect(() => { void load(); }, [chosen, load]);
  useLayoutEffect(() => {
    if (!edited && saved && status) setDraft({ lead: saved.configured.lead, enabled: status.enabled,
      interval: status.config?.interval_minutes ?? status.options.interval.default });
  }, [saved, status, edited]);
  const dirty = !!saved && !!status && (draft.lead !== saved.configured.lead || draft.enabled !== status.enabled
    || (draft.enabled && draft.interval !== (status.config?.interval_minutes ?? status.options.interval.default)));
  const set = (change: Partial<typeof draft>) => { setDraft(d => ({ ...d, ...change })); setEdited(true); setError(''); };
  const ids = [...new Set((status?.options.managers ?? []).map(m => m.id))];
  if (saved?.configured.lead && !ids.includes(saved.configured.lead)) ids.push(saved.configured.lead);
  const capability = status?.options.managers.find(m => m.id === draft.lead);
  const reason = draft.lead === status?.selected_manager.id ? status.selected_manager.reason_text
    : capability && !capability.eligible ? capability.reason : !draft.lead ? 'Choose a project manager before turning on automatic checks.' : '';
  const intervalValid = !!status && Number.isInteger(draft.interval) && draft.interval >= status.options.interval.min
    && draft.interval <= status.options.interval.max;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!saved || !status || !dirty || submitted.current || (draft.enabled && !intervalValid)) return;
    submitted.current = true;
    await run(async () => {
      const next = await runtime<Responders & { checkins: ManagerAuditStatus }>('/responders', {
        lead: draft.lead, backup: draft.lead && draft.lead === saved.configured.backup ? '' : saved.configured.backup,
        checkins: { enabled: draft.enabled, interval_minutes: draft.interval },
      });
      if (!next.checkins || next.checkins.project_id !== project) throw new Error('The open project changed. Reload its manager settings.');
      setSaved(next); setStatus(next.checkins); setEdited(false);
      setDraft({ lead: next.configured.lead, enabled: next.checkins.enabled,
        interval: next.checkins.config?.interval_minutes ?? next.checkins.options.interval.default });
      window.dispatchEvent(new Event(SAVED_EVENT));
      // Save already succeeded. A failed snapshot refresh must not turn it into a refusal.
      void refresh().catch(() => undefined);
    });
    submitted.current = false;
  };
  if (!saved || !status) return loadError ? <ErrorText message={loadError} /> : null;
  return <section className="tm-leader bd-panel" aria-labelledby="tm-manager-heading">
    <div>
      <h2 id="tm-manager-heading">Project manager</h2>
      <p className="tm-leader-current" role="status">{!saved ? 'Loading project manager' : saved.configured.lead
        ? <>Current project manager: <strong>{leaderName(state, saved.configured.lead)}</strong></> : 'No project manager selected'}</p>
      {saved?.configured.lead && state?.agents[saved.configured.lead]?.paused && <p className="bd-muted">This manager is stopped.</p>}
      {saved && saved.lead !== saved.configured.lead && <p className="bd-muted">Available chat responder: {leaderName(state, saved.lead)}. Your project manager selection stays unchanged.</p>}
      <p className="bd-muted" id="tm-manager-help">Your project manager replies to messages without an @ and coordinates the team's work. @all wakes the whole team; @agent addresses that agent.</p>
      <p className="bd-muted">Automatic checks can find idle owners, assign up to two scoped tasks and wake their owners. Planning and those workers share the finite turn budget. Existing Stop and room limits still apply.</p>
      <p className="bd-muted">The project supplies the coordination task, chat and work folders. You don't need to set them up.</p>
      {status && <p className="bd-muted" role="status">Saved automatic checks: {status.enabled ? 'On' : 'Off'}{dirty && ' · Unsaved changes'}</p>}
    </div>
    <form className="tm-leader-controls bd-form" onSubmit={e => void save(e)}>
      <label htmlFor="tm-manager-select">Project manager</label>
      <select id="tm-manager-select" value={draft.lead} disabled={!saved || !status || busy} aria-describedby="tm-manager-help"
        onChange={e => set({ lead: e.target.value })}>
        <option value="">No project manager selected</option>
        {ids.map(id => <option key={id} value={id} disabled={!!state?.agents[id]?.archived}>
          {leaderName(state, id)} · @{state?.agents[id]?.handle || id}{state?.agents[id]?.archived ? ' (archived)' : ''}
        </option>)}
      </select>
      <label className="bd-check">
        <input type="checkbox" checked={draft.enabled} disabled={!status || busy} aria-describedby="tm-manager-reason"
          onChange={e => set({ enabled: e.target.checked })} />
        <span>Check on idle work automatically</span>
      </label>
      <p className="bd-muted" id="tm-manager-reason">{reason || 'Check unfinished project work and wake available owners when there is a next step.'}</p>
      <Field label="Check every (minutes)" hint={status ? `${status.options.interval.min}–${status.options.interval.max} minutes. The first check waits one full interval.` : 'Loading interval'}>
        <input type="number" min={status?.options.interval.min} max={status?.options.interval.max} step={1}
          disabled={!draft.enabled || busy || !status} required={draft.enabled} value={Number.isFinite(draft.interval) ? draft.interval : ''}
          onChange={e => set({ interval: e.target.value === '' ? Number.NaN : Number(e.target.value) })} />
      </Field>
      {dirty && draft.lead === saved?.configured.backup && <p className="bd-muted">This teammate is the backup. Saving makes them project manager and chooses a backup from the default order.</p>}
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={!dirty || busy || (draft.enabled && !intervalValid)}>{busy ? 'Saving...' : 'Save'}</button>
        <button type="button" className="bd-btn" disabled={!dirty || busy} onClick={() => { setEdited(false); setError(''); }}>Discard</button>
      </div>
      {!status && !loadError && <p className="bd-muted" role="status">Loading saved automatic checks...</p>}
      <ErrorText message={error || loadError} />
    </form>
  </section>;
}

/** Board retains status and budget actions, with setup on Team rather than a second manager form. */
export function ManagerAuditPanel({ project }: { project: string }) {
  const { status, setStatus, error: loadError, load } = useManagerStatus(project);
  const { run, busy, error } = useAction();
  const send = (body: Record<string, unknown>) => void run(async () => {
    const next = await runtime<ManagerAuditStatus>('/manager-audit', { project_id: project, ...body });
    if (next.project_id !== project) throw new Error('The open project changed. Reload its manager settings.');
    setStatus(next); window.dispatchEvent(new Event(SAVED_EVENT));
  });
  return <section className="bd-panel" aria-labelledby="bd-manager-heading">
    <h3 id="bd-manager-heading">Project manager</h3>
    <ErrorText message={error || loadError} />
    {!status ? <p className="bd-muted">Loading manager status...</p> : <>
      <p><strong>{status.selected_manager.label || 'No project manager selected'}</strong> · Automatic checks {status.enabled ? 'On' : 'Off'}</p>
      {!status.selected_manager.eligible && <p className="bd-muted">{status.selected_manager.reason_text}</p>}
      <p className="bd-muted">Choose the project manager and automatic checks together on Team.</p>
      <div className="bd-actions">
        <button type="button" className="bd-btn" onClick={openTeam}>Open manager settings on Team</button>
        <button type="button" className="bd-btn" onClick={() => void load()}>Refresh status</button>
        {status.enabled && <button type="button" className="bd-btn" disabled={busy} onClick={() => send({ action: 'disable' })}>Turn off automatic checks</button>}
      </div>
      <dl className="bd-stats">
        <div><dt>Automatic turns remaining</dt><dd>{status.budget ? `${status.budget.remaining} / ${status.budget.total}` : 'Not enabled yet'}</dd></div>
        <div><dt>Next check</dt><dd>{status.enabled && status.next_due ? new Date(status.next_due * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Not scheduled'}</dd></div>
        <div><dt>Last result</dt><dd>{status.last ? <RelativeTime time={status.last.time} /> : 'None yet'}</dd></div>
      </dl>
      <p className="bd-muted" role="status">{status.last ? `${OUTCOMES[status.last.outcome] ?? status.last.outcome}${status.last.detail ? ': ' + status.last.detail : '.'}` : 'No check-ins yet.'}</p>
      {status.disabled_reason && <p className="bd-banner bd-banner-warn">{status.disabled_reason}</p>}
      {status.budget && status.budget.remaining <= 0 && <p className="bd-banner bd-banner-warn">The automatic-work budget is used up. Renew it to continue.</p>}
      {status.budget && status.config && status.generation && <details>
        <summary>Renew automatic-work budget</summary>
        <p className="bd-muted">Planning and its workers share {status.budget.total} turns. Renewal replaces the budget and invalidates old queued checks. It does not turn checks on or off.</p>
        <button type="button" className="bd-btn" disabled={busy} onClick={() => send({ action: 'renew', generation: status.generation })}>Renew budget · {status.budget.total} turns</button>
      </details>}
      <details><summary>Automatic check details</summary>
        <p className="bd-muted">{status.task ? `Coordination: ${status.task.title}. ` : ''}{status.channel ? `Chat: ${status.channel.title}.` : ''}</p>
        <p className="bd-muted">{status.scope.mode === 'folders' ? `Work stays within: ${status.scope.folders.join(', ')}.` : 'Work uses this project’s code folders.'}</p>
        {!!status.scope.excluded.length && <p className="bd-muted">Excluded: {status.scope.excluded.join(', ')}.</p>}
        <p className="bd-muted">Turning checks off and on does not refill the budget. Stop, pause and room limits still apply. Missed checks are not replayed; deliberately deferred work stays deferred.</p>
      </details>
    </>}
  </section>;
}
