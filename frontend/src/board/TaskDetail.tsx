/* Task detail panel: status, ownership, reservations, dependencies, check-ins,
 * reviews and follow-ups. All writes go through POST /api/runtime/tasks.
 * The sections live in ./detail; this component owns status and ownership. */
import { useEffect, useRef, useState } from 'react';
import { runtime } from '../lib/api';
import { sessionTitle, since } from '../lib/room';
import { TASK_STATUSES, type ProjectState, type Task, type TaskStatus } from '../lib/types';
import { TaskAttachments } from './Attachments';
import { isOpenReview } from './Coordination';
import { EditTask } from './detail/EditTask';
import { ExecutionFacts } from './detail/ExecutionFacts';
import { TaskOverview } from './detail/TaskOverview';
import { TaskProgress } from './detail/TaskProgress';
import { TaskEvidence, TaskFollowups, TaskReviews } from './detail/TaskRecords';
import { useResizablePanel } from './detail/useResizablePanel';
import { ErrorText, Field, ParticipantOptions, STATUS_LABELS, StatusPill, displayName, useAction, type Agents } from './shared';
import { executionLine, isStale, waitInfo } from './TaskCard';

const newestFirst = <T extends { updated: number }>(a: T, b: T) => b.updated - a.updated;

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
  const [pendingDone, setPendingDone] = useState(false);
  const [started, setStarted] = useState('');
  const headingRef = useRef<HTMLHeadingElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const resizeHandle = useResizablePanel(panelRef);

  // Reset per-task drafts when switching tasks; keep them across live updates of the same task.
  useEffect(() => {
    setEditing(false);
    setPendingDone(false);
    setStarted('');
    headingRef.current?.focus();
  }, [task.id]);
  useEffect(() => setOwner(task.owner), [task.owner]);

  const byId = new Map(project.tasks.map(t => [t.id, t]));
  const unfinished = (task.depends_on ?? []).map(id => byId.get(id)).filter((t): t is Task => !!t && t.status !== 'done');
  const reviews = (project.reviews ?? []).filter(r => r.task_id === task.id).sort(newestFirst);
  const followups = (project.followups ?? []).filter(f => f.task_id === task.id).sort(newestFirst);
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

  const assigned = owner !== task.owner;

  return (
    <aside ref={panelRef} className="bd-detail" aria-labelledby="bd-detail-title">
      <div className="bd-detail-resize" role="separator" aria-orientation="vertical" aria-label="Resize task details" {...resizeHandle} tabIndex={0}
        title="Drag to resize; use left/right arrows when focused">↔ Resize</div>
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

      <ExecutionFacts task={task} execution={execution} agents={agents} now={now} busy={busy} onAccept={() => void change({ action: 'accept' })} />

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
        <TaskOverview task={task} tasks={project.tasks} onOpenTask={onOpenTask} />
      )}

      <section className="bd-detail-section" aria-labelledby="bd-images-heading">
        <h3 id="bd-images-heading">Images ({task.attachments?.length ?? 0})</h3>
        <TaskAttachments key={task.id} task={task} channel={channel} />
      </section>

      <TaskProgress task={task} agents={agents} now={now} busy={busy} onPost={text => change({ action: 'update', update: text })} />
      <TaskEvidence task={task} />
      <TaskReviews task={task} reviews={reviews} agents={agents} channel={channel} />
      <TaskFollowups task={task} followups={followups} agents={agents} channel={channel} />
    </aside>
  );
}
