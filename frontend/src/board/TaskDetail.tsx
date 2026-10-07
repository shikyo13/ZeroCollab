import { sessionTitle } from '../lib/room';
/* Task detail panel: status, ownership, reservations, dependencies, check-ins,
 * reviews and follow-ups. All writes go through POST /api/runtime/tasks. */
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import { since } from '../lib/room';
import { EVIDENCE_KINDS, TASK_STATUSES, type ProjectState, type Task, type TaskStatus } from '../lib/types';
import { TaskAttachments } from './Attachments';
import { FollowupForm, FollowupItem, RequestReviewForm, ReviewItem, isOpenFollowup, isOpenReview } from './Coordination';
import {
  AgentChip,
  ErrorText,
  Field,
  LabelChips,
  LabelField,
  ParticipantOptions,
  STATUS_LABELS,
  StatusPill,
  displayName,
  splitLabels,
  splitPaths,
  useAction,
  type Agents,
} from './shared';
import { executionLine, isStale, waitInfo } from './TaskCard';

function DependencyPicker({
  task,
  tasks,
  value,
  onChange,
}: {
  task: Task;
  tasks: Task[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const candidates = tasks.filter(t => t.id !== task.id);
  if (!candidates.length) return <p className="bd-muted">No other tasks to depend on.</p>;
  return (
    <fieldset className="bd-dep-picker">
      <legend>Depends on</legend>
      {candidates.map(t => (
        <label key={t.id} className="bd-check">
          <input
            type="checkbox"
            checked={value.includes(t.id)}
            onChange={e => onChange(e.target.checked ? [...value, t.id] : value.filter(id => id !== t.id))}
          />
          <span>
            {t.title} <code className="bd-id">{t.id}</code> <StatusPill status={t.status} />
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function EditTask({ task, tasks, onDone }: { task: Task; tasks: Task[]; onDone: () => void }) {
  const { run, busy, error } = useAction();
  const [title, setTitle] = useState(task.title);
  const [details, setDetails] = useState(task.details);
  const [paths, setPaths] = useState(task.paths.join('\n'));
  const [deps, setDeps] = useState<string[]>(task.depends_on ?? []);
  const [labels, setLabels] = useState((task.labels ?? []).join(', '));
  const knownLabels = [...new Set(tasks.flatMap(t => t.labels ?? []))].sort();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(() =>
      runtime('/tasks', {
        action: 'update',
        task_id: task.id,
        title: title.trim(),
        details: details.trim(),
        paths: splitPaths(paths),
        depends_on: deps,
        // Only sent when edited, so saving other fields never depends on label support.
        ...(splitLabels(labels).join() !== (task.labels ?? []).join() ? { labels: splitLabels(labels) } : {}),
      }),
    );
    if (ok) onDone();
  };
  return (
    <form className="bd-form" onSubmit={submit}>
      <Field label="Title">
        <input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} required />
      </Field>
      <Field label="Details and done criteria">
        <textarea value={details} onChange={e => setDetails(e.target.value)} rows={4} maxLength={3000} />
      </Field>
      <LabelField value={labels} onChange={setLabels} known={knownLabels} />
      <Field label="Reserved paths" hint="One per line or comma-separated. Overlapping reservations with other owned tasks are refused.">
        <textarea value={paths} onChange={e => setPaths(e.target.value)} rows={3} className="bd-mono" />
      </Field>
      <DependencyPicker task={task} tasks={tasks} value={deps} onChange={setDeps} />
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !title.trim()}>
          Save changes
        </button>
        <button type="button" className="bd-btn" onClick={onDone}>
          Cancel
        </button>
      </div>
      <ErrorText message={error} />
    </form>
  );
}

export function TaskDetail({
  task,
  project,
  agents,
  channel,
  now,
  onClose,
  onOpenTask,
}: {
  task: Task;
  project: ProjectState;
  agents: Agents;
  channel: string;
  now: number;
  onClose: () => void;
  onOpenTask: (id: string) => void;
}) {
  const { run, busy, error } = useAction();
  const [editing, setEditing] = useState(false);
  const [owner, setOwner] = useState(task.owner);
  const [note, setNote] = useState('');
  const [pendingDone, setPendingDone] = useState(false);
  const [started, setStarted] = useState('');
  const headingRef = useRef<HTMLHeadingElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [panelWidth, setPanelWidth] = useState(460);
  const resizePanel = (width: number) => setPanelWidth(Math.max(320, Math.min(width, Math.min(900, window.innerWidth - 32))));
  useEffect(() => {
    const parent = panelRef.current?.closest<HTMLElement>('.bd-tasks');
    parent?.style.setProperty('--detail-width', `${panelWidth}px`);
    return () => { parent?.style.removeProperty('--detail-width'); };
  }, [panelWidth]);

  // Reset per-task drafts when switching tasks; keep them across live updates of the same task.
  useEffect(() => {
    setEditing(false);
    setNote('');
    setPendingDone(false);
    setStarted('');
    headingRef.current?.focus();
  }, [task.id]);
  useEffect(() => setOwner(task.owner), [task.owner]);

  const byId = new Map(project.tasks.map(t => [t.id, t]));
  const deps = (task.depends_on ?? []).map(id => byId.get(id)).filter((t): t is Task => !!t);
  const unfinished = deps.filter(t => t.status !== 'done');
  const dependents = project.tasks.filter(t => t.depends_on?.includes(task.id));
  const reviews = (project.reviews ?? []).filter(r => r.task_id === task.id).sort((a, b) => b.updated - a.updated);
  const followups = (project.followups ?? []).filter(f => f.task_id === task.id).sort((a, b) => b.updated - a.updated);
  const openReviews = reviews.filter(isOpenReview);
  const stale = isStale(task, now, project.stale_seconds);
  const execution = executionLine(task, waitInfo(task, project));

  const change = (body: Record<string, unknown>) => run(() => runtime('/tasks', { task_id: task.id, channel, ...body }));

  const setStatus = (status: TaskStatus) => {
    if (status === task.status) return;
    if (status === 'done' && (unfinished.length || openReviews.length)) {
      setPendingDone(true);
      return;
    }
    setPendingDone(false);
    void change({ action: 'update', status });
  };

  const submitNote = async (e: FormEvent) => {
    e.preventDefault();
    if (note.trim() && (await change({ action: 'update', update: note.trim() }))) setNote('');
  };

  const assigned = owner !== task.owner;

  return (
    <aside ref={panelRef} className="bd-detail" aria-labelledby="bd-detail-title">
      <div className="bd-detail-resize" role="separator" aria-orientation="vertical" aria-label="Resize task details" aria-valuenow={panelWidth} tabIndex={0}
        onPointerDown={e => { drag.current = { x: e.clientX, width: panelWidth }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={e => { if (drag.current) resizePanel(drag.current.width + drag.current.x - e.clientX); }}
        onPointerUp={e => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); }}
        onPointerCancel={() => { drag.current = null; }}
        onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); resizePanel(panelWidth + (e.key === 'ArrowLeft' ? 40 : -40)); } }} title="Drag to resize; use left/right arrows when focused">↔ Resize</div>
      <header className="bd-detail-head">
        <div>
          <h2 id="bd-detail-title" tabIndex={-1} ref={headingRef}>
            {task.title}
          </h2>
          <p className="bd-muted">
            <code className="bd-id">{task.id}</code> · <StatusPill status={task.status} /> ·{' '}
            <span title={new Date(task.updated * 1000).toLocaleString()}>updated {since(task.updated, now)}</span>
          </p>
        </div>
        <button type="button" className="bd-btn bd-btn-quiet bd-close" onClick={onClose} aria-label="Close task details">
          ×
        </button>
      </header>

      {stale && (
        <p className="bd-banner bd-banner-warn">
          No check-in for over {Math.round(project.stale_seconds / 60)} minutes while {STATUS_LABELS[task.status].toLowerCase()}.
        </p>
      )}

      {task.status !== 'done' && (execution || task.next_action || task.priority || task.transfer) && (
        <section className="bd-detail-section" aria-label="Execution">
          <dl className="bd-facts">
            {execution && (
              <>
                <dt>Now</dt>
                <dd className={`bd-exec bd-exec-${execution.tone}`}>
                  <span className="bd-exec-dot" aria-hidden="true" />
                  {execution.text}
                </dd>
              </>
            )}
            {task.next_action && (
              <>
                <dt>{task.execution_state === 'waiting_on_human' ? 'Then' : 'Next action'}</dt>
                <dd>{task.next_action}</dd>
              </>
            )}
            {task.priority && (
              <>
                <dt>Priority</dt>
                <dd>{task.priority}{task.priority === 1 ? ' (highest)' : ''}</dd>
              </>
            )}
            {task.transfer && (
              <>
                <dt>Ownership</dt>
                <dd>
                  {displayName(task.transfer.from, agents)} proposed handing it to {displayName(task.transfer.to, agents)}{' '}
                  {since(task.transfer.requested_at, now)}. {displayName(task.owner, agents) || 'Nobody'} owns it until{' '}
                  {displayName(task.transfer.to, agents)} accepts.{' '}
                  <button type="button" className="bd-btn" disabled={busy} onClick={() => void change({ action: 'accept' })}>
                    Accept for {displayName(task.transfer.to, agents)}
                  </button>
                </dd>
              </>
            )}
          </dl>
        </section>
      )}

      <section className="bd-detail-section" aria-label="Status and owner">
        <div className="bd-form-row">
          <Field label="Status">
            <select value={pendingDone ? 'done' : task.status} onChange={e => setStatus(e.target.value as TaskStatus)} disabled={busy}>
              {TASK_STATUSES.map(s => (
                <option key={s} value={s}>
                  {STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Owner">
            <select value={owner} onChange={e => setOwner(e.target.value)} disabled={busy}>
              <ParticipantOptions agents={agents} current={task.owner} emptyLabel="Unclaimed" />
            </select>
          </Field>
        </div>
        {pendingDone && (
          <div className="bd-banner bd-banner-warn" role="alert">
            <p>
              {unfinished.length > 0 && `${unfinished.length} unfinished ${unfinished.length === 1 ? 'dependency' : 'dependencies'}. `}
              {openReviews.length > 0 && `${openReviews.length} open ${openReviews.length === 1 ? 'review' : 'reviews'}. `}
              Agents cannot close this task yet; you can override.
            </p>
            <div className="bd-actions">
              <button
                type="button"
                className="bd-btn bd-btn-danger"
                disabled={busy}
                onClick={() => void change({ action: 'update', status: 'done' }).then(() => setPendingDone(false))}
              >
                Mark done anyway
              </button>
              <button type="button" className="bd-btn" onClick={() => setPendingDone(false)}>
                Keep {STATUS_LABELS[task.status].toLowerCase()}
              </button>
            </div>
          </div>
        )}
        <div className="bd-actions">
          {assigned && (
            <button
              type="button"
              className="bd-btn bd-btn-primary"
              disabled={busy}
              onClick={() => void change(owner ? { action: 'delegate', owner } : { action: 'release' })}
            >
              {owner ? `Assign to ${displayName(owner, agents)}` : 'Release ownership'}
            </button>
          )}
          {!assigned && task.owner && task.status !== 'done' && (
            <>
              <button
                type="button"
                className="bd-btn bd-btn-primary"
                disabled={busy}
                title={`Queue a turn for ${displayName(task.owner, agents)} in ${sessionTitle(channel)} with this task and its reserved paths`}
                onClick={() =>
                  void change({ action: 'update', start: true }).then(ok => ok && setStarted(`Sent to ${displayName(task.owner, agents)} in ${sessionTitle(channel)}.`))
                }
              >
                Start {displayName(task.owner, agents)} on this
              </button>
              <button type="button" className="bd-btn" disabled={busy} onClick={() => void change({ action: 'release' })}>
                Release
              </button>
            </>
          )}
          <button type="button" className="bd-btn" onClick={() => setEditing(v => !v)} aria-expanded={editing}>
            {editing ? 'Close editor' : 'Edit task'}
          </button>
        </div>
        {started && (
          <p className="bd-muted" role="status">
            {started}
          </p>
        )}
        <ErrorText message={error} />
      </section>

      {editing ? (
        <section className="bd-detail-section">
          <h3>Edit task</h3>
          <EditTask task={task} tasks={project.tasks} onDone={() => setEditing(false)} />
        </section>
      ) : (
        <section className="bd-detail-section">
          {task.details ? <p className="bd-prose">{task.details}</p> : <p className="bd-muted">No details yet.</p>}
          {task.labels?.length ? (
            <>
              <h3>Areas</h3>
              <LabelChips labels={task.labels} />
            </>
          ) : null}
          <h3>Reserved paths</h3>
          {task.paths.length ? (
            <ul className="bd-paths bd-paths-full">
              {task.paths.map(p => (
                <li key={p}>
                  <code>{p}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="bd-muted">None - discussion or research. Agents must reserve paths before editing.</p>
          )}
          {(deps.length > 0 || dependents.length > 0) && (
            <>
              <h3>Dependencies</h3>
              <ul className="bd-dep-list">
                {deps.map(t => (
                  <li key={t.id}>
                    <span className="bd-muted">Needs</span>{' '}
                    <button type="button" className="bd-link" onClick={() => onOpenTask(t.id)}>
                      {t.title}
                    </button>{' '}
                    <StatusPill status={t.status} />
                  </li>
                ))}
                {dependents.map(t => (
                  <li key={t.id}>
                    <span className="bd-muted">Unblocks</span>{' '}
                    <button type="button" className="bd-link" onClick={() => onOpenTask(t.id)}>
                      {t.title}
                    </button>{' '}
                    <StatusPill status={t.status} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <section className="bd-detail-section" aria-labelledby="bd-images-heading">
        <h3 id="bd-images-heading">Images ({task.attachments?.length ?? 0})</h3>
        <TaskAttachments key={task.id} task={task} channel={channel} />
      </section>

      <section className="bd-detail-section" aria-labelledby="bd-checkin-heading">
        <h3 id="bd-checkin-heading">Progress</h3>
        <form className="bd-form" onSubmit={submitNote}>
          <Field label="Post a check-in" hint="Progress, blocker or review note. Visible to the team and the owner.">
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={1600} />
          </Field>
          <div className="bd-actions">
            <button type="submit" className="bd-btn" disabled={busy || !note.trim()}>
              Post check-in
            </button>
          </div>
        </form>
        {task.updates.length > 0 ? (
          <ol className="bd-history">
            {[...task.updates].reverse().map((u, i) => (
              <li key={`${u.time}-${i}`} className={u.auto ? 'bd-history-event' : undefined}>
                <div className="bd-history-head">
                  <AgentChip name={u.by} agents={agents} />
                  {u.event && !u.auto && <span className="bd-feed-tag">{u.event}</span>}
                  <span className="bd-muted" title={new Date(u.time * 1000).toLocaleString()}>
                    {since(u.time, now)}
                  </span>
                </div>
                <p>{u.auto ? u.event ?? u.text : u.text}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="bd-muted">No history yet.</p>
        )}
      </section>

      {(Object.keys(task.evidence ?? {}).length > 0 || task.status === 'review' || task.status === 'done') && (
        <section className="bd-detail-section" aria-labelledby="bd-task-evidence">
          <h3 id="bd-task-evidence">Evidence</h3>
          <dl className="bd-facts">
            {EVIDENCE_KINDS.map(([kind, label]) => (
              <div key={kind} className="bd-facts-row">
                <dt>{label}</dt>
                <dd className={task.evidence?.[kind] ? '' : 'bd-muted'}>{task.evidence?.[kind] || 'Not recorded'}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section className="bd-detail-section" aria-labelledby="bd-task-reviews">
        <h3 id="bd-task-reviews">Reviews ({openReviews.length} open)</h3>
        {reviews.length > 0 && (
          <ul className="bd-records">
            {reviews.map(r => (
              <ReviewItem key={r.id} review={r} agents={agents} channel={channel} />
            ))}
          </ul>
        )}
        <details className="bd-disclosure">
          <summary>Request a review</summary>
          <RequestReviewForm task={task} agents={agents} channel={channel} />
        </details>
      </section>

      <section className="bd-detail-section" aria-labelledby="bd-task-followups">
        <h3 id="bd-task-followups">Follow-ups ({followups.filter(isOpenFollowup).length} open)</h3>
        {followups.length > 0 && (
          <ul className="bd-records">
            {followups.map(f => (
              <FollowupItem key={f.id} followup={f} agents={agents} channel={channel} />
            ))}
          </ul>
        )}
        <details className="bd-disclosure">
          <summary>Add a follow-up</summary>
          <FollowupForm agents={agents} channel={channel} taskId={task.id} />
        </details>
      </section>
    </aside>
  );
}
