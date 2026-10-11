/* Project manager check-ins: the user's opt-in for systems' recurring manager audit (/api/runtime/manager-audit,
 * contract 5c85e933). The server owns scheduling, eligibility, the finite budget and the outcome record; this panel
 * shows its status and sends only the user's explicit enable, change, turn-off and renew actions (design 90e41648). */
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import { sessionTitle } from '../lib/room';
import { ErrorText, Field, RelativeTime, useAction } from './shared';

export interface ManagerAuditConfig {
  manager: string;
  task_id: string;
  channel: string;
  interval_minutes: number;
}

export interface ManagerAuditStatus {
  project_id: string;
  enabled: boolean;
  /** Opaque; sent back unchanged with renew so a stale page can't renew a replaced configuration. */
  generation: string | null;
  config: ManagerAuditConfig | null;
  options: {
    managers: { id: string; label: string; eligible: boolean; reason?: string }[];
    tasks: { id: string; title: string; owner?: string }[];
    channels: { id: string; title?: string }[];
    interval: { min: number; max: number; default: number };
  };
  /** Fixed by the server; used includes reserved and uncertain starts and every downstream turn. */
  budget: { total: number; used: number; remaining: number; per_hour: number; shared_per_hour: number } | null;
  next_due: number | null;
  last: { time: number; outcome: 'audited' | 'no_findings' | 'covered' | 'refused' | 'failed'; detail?: string } | null;
  disabled_reason: string | null;
}

const OUTCOMES: Record<NonNullable<ManagerAuditStatus['last']>['outcome'], string> = {
  audited: 'Checked in',
  no_findings: 'Nothing needed attention; no agent turn started',
  covered: 'Skipped: the manager was already working',
  refused: 'Refused',
  failed: 'Did not finish',
};

const ERROR_ID = 'bd-manager-error';

/** The saved configuration, or an empty draft: nothing is chosen for the user except the suggested interval. */
function draftFrom(status: ManagerAuditStatus): ManagerAuditConfig {
  return status.config ?? { manager: '', task_id: '', channel: '', interval_minutes: status.options.interval.default };
}

export function ManagerAuditPanel({ project }: { project: string }) {
  const [status, setStatus] = useState<ManagerAuditStatus | null>(null);
  const [loadError, setLoadError] = useState('');
  const [draft, setDraft] = useState<ManagerAuditConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState('');
  const { run, busy, error } = useAction();

  const apply = useCallback((next: ManagerAuditStatus, resetDraft: boolean) => {
    setStatus(next);
    if (resetDraft) {
      setDraft(draftFrom(next));
      setDirty(false);
    }
  }, []);

  // Read through a ref so editing doesn't refetch, and a refetch never overwrites unsaved choices.
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  // The board remounts this panel per project (key), so status, draft and actions never carry over. A response for
  // a different project than the one shown is refused instead of displayed (a late reply after a switch).
  const forThisProject = (next: ManagerAuditStatus) => !project || next.project_id === project;
  const load = useCallback(async () => {
    try {
      const next = await runtime<ManagerAuditStatus>('/manager-audit');
      if (!forThisProject(next)) {
        setLoadError('The open project changed. Reload the board to see its check-ins.');
        return;
      }
      apply(next, !dirtyRef.current);
      setLoadError('');
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [apply, project]); // eslint-disable-line react-hooks/exhaustive-deps -- forThisProject depends only on project

  useEffect(() => {
    void load();
  }, [load]);

  // Actions go to the project whose status is shown, and the server refuses a project that is no longer open (409).
  // Only a successful response changes the saved status; a refusal keeps every field as the user left it.
  const send = async (label: string, body: Record<string, unknown>) => {
    if (!status) return;
    setPending(label);
    await run(async () => {
      const next = await runtime<ManagerAuditStatus>('/manager-audit', { project_id: status.project_id, ...body });
      if (!forThisProject(next)) throw new Error('The open project changed. Reload the board to see its check-ins.');
      apply(next, true);
    });
    setPending('');
  };

  if (!status || !draft) {
    return (
      <section className="bd-panel" aria-labelledby="bd-manager-heading">
        <h3 id="bd-manager-heading">Project manager check-ins (optional)</h3>
        {loadError ? <ErrorText message={loadError} /> : <p className="bd-muted" role="status">Loading check-in status…</p>}
      </section>
    );
  }

  const { options, budget } = status;
  const manager = options.managers.find(m => m.id === draft.manager);
  const tasks = draft.manager ? options.tasks.filter(t => t.owner === draft.manager) : [];
  const interval = options.interval;
  const intervalValid = Number.isInteger(draft.interval_minutes)
    && draft.interval_minutes >= interval.min && draft.interval_minutes <= interval.max;
  const complete = !!(manager?.eligible && draft.task_id && draft.channel && intervalValid);
  const set = (change: Partial<ManagerAuditConfig>) => {
    setDraft(d => (d ? { ...d, ...change } : d));
    setDirty(true);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (complete) void send(status.enabled ? 'Saving changes…' : 'Enabling check-ins…', { action: 'enable', ...draft });
  };
  const describedBy = error ? ERROR_ID : undefined;

  return (
    <section className="bd-panel" aria-labelledby="bd-manager-heading">
      <h3 id="bd-manager-heading">
        Project manager check-ins (optional) <span className="bd-muted">{status.enabled ? 'On' : 'Off'}</span>
      </h3>
      <p className="bd-muted">
        Periodically check this project's unfinished work for unassigned tasks, idle owners and unresolved handoffs. A
        check with nothing to act on starts no agent turn.
      </p>

      <form className="bd-form" onSubmit={submit}>
        <div className="bd-limits-grid">
          <Field label="Project manager" hint={manager && !manager.eligible ? manager.reason : 'Choose a registered manager with a supported restricted runner.'}>
            <select
              value={draft.manager}
              aria-describedby={describedBy}
              required
              // A different manager owns different tasks: the task and chat must be chosen again, never substituted.
              onChange={e => set({ manager: e.target.value, task_id: '', channel: '' })}
            >
              <option value="">Choose a manager</option>
              {options.managers.map(m => (
                <option key={m.id} value={m.id} disabled={!m.eligible}>
                  {m.label}
                  {m.eligible ? '' : ' (not supported)'}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Coordination task" hint="An unfinished task owned by the selected manager, in this project.">
            <select value={draft.task_id} aria-describedby={describedBy} required onChange={e => set({ task_id: e.target.value })}>
              <option value="">{draft.manager ? 'Choose a task' : 'Choose a manager first'}</option>
              {tasks.map(t => (
                <option key={t.id} value={t.id}>
                  {t.title} · {t.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Check-in chat" hint="An existing ordinary chat owned by the manager.">
            <select value={draft.channel} aria-describedby={describedBy} required onChange={e => set({ channel: e.target.value })}>
              <option value="">Choose a chat</option>
              {options.channels.map(c => (
                <option key={c.id} value={c.id}>
                  {c.title || sessionTitle(c.id)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Check every (minutes)" hint={`${interval.min}-${interval.max} minutes, suggested ${interval.default}. First check waits one full interval.`}>
            <input
              type="number"
              min={interval.min}
              max={interval.max}
              step={1}
              required
              aria-describedby={describedBy}
              value={Number.isFinite(draft.interval_minutes) ? draft.interval_minutes : ''}
              onChange={e => set({ interval_minutes: e.target.value === '' ? Number.NaN : Math.trunc(Number(e.target.value)) })}
            />
          </Field>
        </div>
        <div className="bd-actions">
          {(!status.enabled || dirty) && (
            <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !complete}>
              {status.enabled ? 'Save changes' : 'Enable check-ins'}
            </button>
          )}
          {status.enabled && (
            <button type="button" className="bd-btn" disabled={busy} onClick={() => void send('Turning off…', { action: 'disable' })}>
              Turn off
            </button>
          )}
          {dirty && (
            <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => { setDraft(draftFrom(status)); setDirty(false); }}>
              Discard changes
            </button>
          )}
          {pending && <span className="bd-muted" role="status">{pending}</span>}
        </div>
        {error && (
          <p id={ERROR_ID} className="bd-error" role="alert">
            {error}
          </p>
        )}
      </form>

      <dl className="bd-stats">
        <div>
          <dt>Automatic-chain turns remaining</dt>
          <dd>{budget ? `${budget.remaining} / ${budget.total}` : 'Not enabled yet'}</dd>
        </div>
        <div>
          <dt>Next check</dt>
          <dd>
            {status.enabled && status.next_due
              ? new Date(status.next_due * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
              : 'Not scheduled'}
          </dd>
        </div>
        <div>
          <dt>Last result</dt>
          <dd>{status.last ? <RelativeTime time={status.last.time} /> : 'None yet'}</dd>
        </div>
      </dl>
      <p className="bd-muted" role="status">
        {status.last
          ? `${OUTCOMES[status.last.outcome] ?? status.last.outcome}${status.last.detail ? `: ${status.last.detail}` : '.'}`
          : 'No check-ins yet.'}
      </p>
      {status.disabled_reason && <p className="bd-banner bd-banner-warn">{status.disabled_reason}</p>}
      {budget && budget.remaining <= 0 && (
        <p className="bd-banner bd-banner-warn">The check-in budget is used up. No more check-ins run until you renew it.</p>
      )}
      <p className="bd-muted">
        {budget
          ? `At most ${budget.per_hour} check-ins an hour, within the room's shared limit of ${budget.shared_per_hour} automatic turns an hour. `
          : 'First enablement grants a finite budget of automatic-chain turns, including handoff recipients. '}
        Turning check-ins off and on does not refill the budget. Room limits can stop it earlier. Stop all and manager pause
        suppress check-ins; Resume waits a full interval and missed checks are not replayed. Deliberately deferred work
        stays deferred.
      </p>

      {budget && status.config && status.generation && (
        <details>
          <summary>Renew check-in budget</summary>
          <p className="bd-muted">
            Replace the current budget with {budget.total} automatic-chain turns. Old queued check-ins are invalidated.
            Renewing doesn't turn check-ins on or off; it only replaces the budget, and only when you ask.
          </p>
          <button
            type="button"
            className="bd-btn"
            disabled={busy}
            onClick={() => void send('Renewing budget…', { action: 'renew', generation: status.generation })}
          >
            Renew budget · {budget.total} turns
          </button>
        </details>
      )}
    </section>
  );
}
