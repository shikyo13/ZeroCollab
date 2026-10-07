/* Check-in form and the task's update history, newest first. */
import { useEffect, useState, type FormEvent } from 'react';
import { LIMITS } from '../../lib/constants';
import { since } from '../../lib/room';
import type { Task } from '../../lib/types';
import { AgentChip, Field, type Agents } from '../shared';

export function TaskProgress({
  task,
  agents,
  now,
  busy,
  onPost,
}: {
  task: Task;
  agents: Agents;
  now: number;
  busy: boolean;
  /** Resolves true once the check-in is recorded. */
  onPost: (text: string) => Promise<boolean>;
}) {
  const [note, setNote] = useState('');
  // A half-written check-in belongs to the task it was written for.
  useEffect(() => setNote(''), [task.id]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (note.trim() && (await onPost(note.trim()))) setNote('');
  };
  return (
    <section className="bd-detail-section" aria-labelledby="bd-checkin-heading">
      <h3 id="bd-checkin-heading">Progress</h3>
      <form className="bd-form" onSubmit={submit}>
        <Field label="Post a check-in" hint="Progress, blocker or review note. Visible to the team and the owner.">
          <textarea value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={LIMITS.checkIn} />
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
  );
}
