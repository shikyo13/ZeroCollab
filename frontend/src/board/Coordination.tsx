/* Peer reviews and follow-ups (POST /api/runtime/reviews|followups).
 * The human acts as "user": may request/cancel any review, respond when
 * assigned as reviewer, and create/resolve/cancel any follow-up. */
import { useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import { since } from '../lib/room';
import type { Followup, ProjectState, Review, Task } from '../lib/types';
import { AgentChip, ErrorText, Field, ParticipantOptions, ReviewPill, displayName, useAction, type Agents } from './shared';
import { LIMITS, USER } from '../lib/constants';

const OPEN_REVIEW = new Set(['pending', 'changes_requested']);

export const isOpenReview = (r: Review) => OPEN_REVIEW.has(r.status);
export const isOpenFollowup = (f: Followup) => f.status === 'open';

function WakeToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="bd-check">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      Wake the recipient now (otherwise it waits in their inbox)
    </label>
  );
}

/** Inline "reason/result" prompt used by cancel and resolve actions. */
function InlineReason({
  label,
  placeholder,
  submitLabel,
  onSubmit,
  onCancel,
  busy,
}: {
  label: string;
  placeholder: string;
  submitLabel: string;
  onSubmit: (text: string) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [text, setText] = useState('');
  return (
    <form
      className="bd-inline-form"
      onSubmit={e => {
        e.preventDefault();
        if (text.trim()) onSubmit(text.trim());
      }}
    >
      <Field label={label}>
        <input value={text} onChange={e => setText(e.target.value)} placeholder={placeholder} maxLength={LIMITS.request} required autoFocus />
      </Field>
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !text.trim()}>
          {submitLabel}
        </button>
        <button type="button" className="bd-btn" onClick={onCancel}>
          Back
        </button>
      </div>
    </form>
  );
}

/** What the server hashed when the review was requested. The revision text itself is not verified. */
function ReviewSnapshot({ review }: { review: Review }) {
  const files = review.snapshot?.files ?? [];
  if (!review.snapshot) return null;
  if (!files.length) {
    return <p className="bd-record-meta bd-muted">No files hashed; only the revision reference identifies what was reviewed.</p>;
  }
  const changed = new Set(review.changed_files ?? []);
  return (
    <p className="bd-record-meta" title={files.map(f => `${f.path} ${f.sha256.slice(0, 12)}`).join('\n')}>
      Hashed in <code>{review.snapshot.root}</code>:{' '}
      {files.map((f, i) => (
        <span key={f.path}>
          {i > 0 && ' · '}
          <code>{f.path}</code>{' '}
          {changed.has(f.path) ? <span className="bd-flag-danger">changed since request</span> : <span className="bd-ok">unchanged</span>}
        </span>
      ))}
    </p>
  );
}

export function ReviewItem({
  review,
  task,
  agents,
  channel,
  onOpenTask,
}: {
  review: Review;
  task?: Task;
  agents: Agents;
  channel: string;
  onOpenTask?: (id: string) => void;
}) {
  const { run, busy, error } = useAction();
  const [mode, setMode] = useState<'' | 'cancel' | 'respond'>('');
  const [verdict, setVerdict] = useState<'approved' | 'changes_requested'>('approved');
  const [findings, setFindings] = useState('');
  const send = (body: Record<string, unknown>) =>
    run(() => runtime('/reviews', { ...body, review_id: review.id, channel: review.channel || channel })).then(ok => {
      if (ok) setMode('');
    });
  const canRespond = review.reviewer === USER && review.status === 'pending';
  return (
    <li className="bd-record">
      <div className="bd-record-head">
        <ReviewPill status={review.status} />
        <span>
          <AgentChip name={review.reviewer} agents={agents} /> reviewing{' '}
          {task && onOpenTask ? (
            <button type="button" className="bd-link" onClick={() => onOpenTask(task.id)}>
              {task.title}
            </button>
          ) : (
            task && <strong> {task.title}</strong>
          )}
        </span>
        <span className="bd-muted" title={new Date(review.updated * 1000).toLocaleString()}>
          {since(review.updated)}
        </span>
      </div>
      <p className="bd-record-meta">
        Revision <code>{review.revision}</code> · requested by {displayName(review.requested_by, agents)} · <code>{review.id}</code>
      </p>
      <p className="bd-record-text">{review.request}</p>
      <ReviewSnapshot review={review} />
      {review.findings && (
        <div className="bd-findings">
          <strong>Findings</strong>
          <p>{review.findings}</p>
        </div>
      )}
      {review.cancellation && <p className="bd-muted">Cancelled: {review.cancellation}</p>}
      {mode === 'cancel' && (
        <InlineReason
          label="Why cancel this review?"
          placeholder="e.g. superseded by a new revision"
          submitLabel="Cancel review"
          busy={busy}
          onCancel={() => setMode('')}
          onSubmit={text => void send({ action: 'cancel', text })}
        />
      )}
      {mode === 'respond' && (
        <form
          className="bd-inline-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (findings.trim()) void send({ action: 'respond', revision: review.revision, verdict, text: findings.trim() });
          }}
        >
          <fieldset className="bd-radio-row">
            <legend>Verdict</legend>
            <label>
              <input type="radio" checked={verdict === 'approved'} onChange={() => setVerdict('approved')} /> Approve
            </label>
            <label>
              <input type="radio" checked={verdict === 'changes_requested'} onChange={() => setVerdict('changes_requested')} />{' '}
              Request changes
            </label>
          </fieldset>
          <Field label="Findings and evidence">
            <textarea value={findings} onChange={e => setFindings(e.target.value)} rows={3} maxLength={LIMITS.reviewFindings} required />
          </Field>
          <div className="bd-actions">
            <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !findings.trim()}>
              Submit review
            </button>
            <button type="button" className="bd-btn" onClick={() => setMode('')}>
              Back
            </button>
          </div>
        </form>
      )}
      {!mode && isOpenReview(review) && (
        <div className="bd-actions">
          {canRespond && (
            <button type="button" className="bd-btn bd-btn-primary" onClick={() => setMode('respond')}>
              Respond
            </button>
          )}
          <button type="button" className="bd-btn bd-btn-quiet" onClick={() => setMode('cancel')}>
            Cancel request
          </button>
        </div>
      )}
      <ErrorText message={error} />
    </li>
  );
}

export function FollowupItem({
  followup,
  task,
  agents,
  channel,
  onOpenTask,
}: {
  followup: Followup;
  task?: Task;
  agents: Agents;
  channel: string;
  onOpenTask?: (id: string) => void;
}) {
  const { run, busy, error } = useAction();
  const [mode, setMode] = useState<'' | 'resolve' | 'cancel'>('');
  const send = (action: 'resolve' | 'cancel', result: string) =>
    run(() => runtime('/followups', { action, followup_id: followup.id, result, channel: followup.channel || channel })).then(ok => {
      if (ok) setMode('');
    });
  return (
    <li className="bd-record">
      <div className="bd-record-head">
        <span className={`bd-pill bd-followup-${followup.status}`}>{followup.status === 'open' ? 'Open' : followup.status === 'resolved' ? 'Resolved' : 'Cancelled'}</span>
        <span>
          for <AgentChip name={followup.assigned_to} agents={agents} />
          {task && onOpenTask && (
            <>
              {' '}
              on{' '}
              <button type="button" className="bd-link" onClick={() => onOpenTask(task.id)}>
                {task.title}
              </button>
            </>
          )}
        </span>
        <span className="bd-muted" title={new Date(followup.updated * 1000).toLocaleString()}>
          {since(followup.updated)}
        </span>
      </div>
      <p className="bd-record-meta">
        From {displayName(followup.created_by, agents)} · <code>{followup.id}</code>
      </p>
      <p className="bd-record-text">{followup.text}</p>
      {followup.result && (
        <div className="bd-findings">
          <strong>{followup.status === 'cancelled' ? 'Reason' : 'Result'}</strong>
          <p>{followup.result}</p>
        </div>
      )}
      {mode && (
        <InlineReason
          label={mode === 'resolve' ? 'Result' : 'Why cancel?'}
          placeholder={mode === 'resolve' ? 'What was done or decided' : 'e.g. no longer needed'}
          submitLabel={mode === 'resolve' ? 'Resolve' : 'Cancel follow-up'}
          busy={busy}
          onCancel={() => setMode('')}
          onSubmit={text => void send(mode, text)}
        />
      )}
      {!mode && followup.status === 'open' && (
        <div className="bd-actions">
          <button type="button" className="bd-btn" onClick={() => setMode('resolve')}>
            Resolve
          </button>
          <button type="button" className="bd-btn bd-btn-quiet" onClick={() => setMode('cancel')}>
            Cancel
          </button>
        </div>
      )}
      <ErrorText message={error} />
    </li>
  );
}

export function RequestReviewForm({ task, agents, channel }: { task: Task; agents: Agents; channel: string }) {
  const { run, busy, error } = useAction();
  const [reviewer, setReviewer] = useState('');
  const [revision, setRevision] = useState('');
  const [text, setText] = useState('');
  const [notify, setNotify] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(() =>
      runtime('/reviews', { action: 'request', task_id: task.id, reviewer, revision: revision.trim(), text: text.trim(), notify, channel }),
    );
    if (ok) {
      setRevision('');
      setText('');
    }
  };
  return (
    <form className="bd-form" onSubmit={submit}>
      <div className="bd-form-row">
        <Field label="Reviewer">
          <select value={reviewer} onChange={e => setReviewer(e.target.value)} required>
            <ParticipantOptions agents={agents} includeUser exclude={task.owner ? [task.owner] : []} emptyLabel="Choose a peer" />
          </select>
        </Field>
        <Field label="Revision" hint="Commit, patch digest or message ID">
          <input value={revision} onChange={e => setRevision(e.target.value)} maxLength={LIMITS.reviewRevision} required />
        </Field>
      </div>
      <Field label="What should they check?">
        <textarea value={text} onChange={e => setText(e.target.value)} rows={2} maxLength={LIMITS.request} required />
      </Field>
      <WakeToggle checked={notify} onChange={setNotify} />
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !reviewer || !revision.trim() || !text.trim()}>
          Request review
        </button>
      </div>
      <ErrorText message={error} />
    </form>
  );
}

export function FollowupForm({
  agents,
  channel,
  taskId = '',
  tasks,
}: {
  agents: Agents;
  channel: string;
  taskId?: string;
  tasks?: Task[];
}) {
  const { run, busy, error } = useAction();
  const [assignee, setAssignee] = useState('');
  const [linkedTask, setLinkedTask] = useState(taskId);
  const [text, setText] = useState('');
  const [notify, setNotify] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(() =>
      runtime('/followups', { action: 'create', assigned_to: assignee, task_id: linkedTask, text: text.trim(), notify, channel }),
    );
    if (ok) setText('');
  };
  return (
    <form className="bd-form" onSubmit={submit}>
      <div className="bd-form-row">
        <Field label="Assign to">
          <select value={assignee} onChange={e => setAssignee(e.target.value)} required>
            <ParticipantOptions agents={agents} includeUser emptyLabel="Choose someone" />
          </select>
        </Field>
        {tasks && !taskId && (
          <Field label="Linked task (optional)">
            <select value={linkedTask} onChange={e => setLinkedTask(e.target.value)}>
              <option value="">No task</option>
              {tasks
                .filter(t => t.status !== 'done')
                .map(t => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
            </select>
          </Field>
        )}
      </div>
      <Field label="Next action or question">
        <textarea value={text} onChange={e => setText(e.target.value)} rows={2} maxLength={LIMITS.request} required />
      </Field>
      <WakeToggle checked={notify} onChange={setNotify} />
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !assignee || !text.trim()}>
          Add follow-up
        </button>
      </div>
      <ErrorText message={error} />
    </form>
  );
}

/** Board tab listing every review and follow-up, with the human's own queue first. */
export function CoordinationView({
  project,
  agents,
  channel,
  onOpenTask,
}: {
  project: ProjectState;
  agents: Agents;
  channel: string;
  onOpenTask: (id: string) => void;
}) {
  const [showClosed, setShowClosed] = useState(false);
  const tasks = new Map(project.tasks.map(t => [t.id, t]));
  const reviews = [...(project.reviews ?? [])].sort((a, b) => b.updated - a.updated);
  const followups = [...(project.followups ?? [])].sort((a, b) => b.updated - a.updated);
  const mineReviews = reviews.filter(r => r.reviewer === USER && r.status === 'pending');
  const mineFollowups = followups.filter(f => f.assigned_to === USER && f.status === 'open');
  const shownReviews = reviews.filter(r => (showClosed ? true : isOpenReview(r)) && !mineReviews.includes(r));
  const shownFollowups = followups.filter(f => (showClosed ? true : isOpenFollowup(f)) && !mineFollowups.includes(f));
  const [adding, setAdding] = useState(false);
  return (
    <div className="bd-coordination">
      <div className="bd-toolbar">
        <label className="bd-check">
          <input type="checkbox" checked={showClosed} onChange={e => setShowClosed(e.target.checked)} />
          Show closed and superseded
        </label>
        <button type="button" className="bd-btn" onClick={() => setAdding(v => !v)} aria-expanded={adding}>
          {adding ? 'Close' : 'New follow-up'}
        </button>
      </div>
      {adding && (
        <section className="bd-panel">
          <h3>New follow-up</h3>
          <FollowupForm agents={agents} channel={channel} tasks={project.tasks} />
        </section>
      )}
      {(mineReviews.length > 0 || mineFollowups.length > 0) && (
        <section className="bd-panel bd-panel-attention" aria-labelledby="bd-needs-you">
          <h3 id="bd-needs-you">Waiting on you</h3>
          <ul className="bd-records">
            {mineReviews.map(r => (
              <ReviewItem key={r.id} review={r} task={tasks.get(r.task_id)} agents={agents} channel={channel} onOpenTask={onOpenTask} />
            ))}
            {mineFollowups.map(f => (
              <FollowupItem key={f.id} followup={f} task={tasks.get(f.task_id)} agents={agents} channel={channel} onOpenTask={onOpenTask} />
            ))}
          </ul>
        </section>
      )}
      <div className="bd-two-col">
        <section className="bd-panel" aria-labelledby="bd-reviews-heading">
          <h3 id="bd-reviews-heading">Reviews ({shownReviews.length})</h3>
          {shownReviews.length ? (
            <ul className="bd-records">
              {shownReviews.map(r => (
                <ReviewItem key={r.id} review={r} task={tasks.get(r.task_id)} agents={agents} channel={channel} onOpenTask={onOpenTask} />
              ))}
            </ul>
          ) : (
            <p className="bd-empty">No {showClosed ? '' : 'open '}reviews. Request one from a task's detail panel.</p>
          )}
        </section>
        <section className="bd-panel" aria-labelledby="bd-followups-heading">
          <h3 id="bd-followups-heading">Follow-ups ({shownFollowups.length})</h3>
          {shownFollowups.length ? (
            <ul className="bd-records">
              {shownFollowups.map(f => (
                <FollowupItem key={f.id} followup={f} task={tasks.get(f.task_id)} agents={agents} channel={channel} onOpenTask={onOpenTask} />
              ))}
            </ul>
          ) : (
            <p className="bd-empty">No {showClosed ? '' : 'open '}follow-ups.</p>
          )}
        </section>
      </div>
    </div>
  );
}
