/* What is happening with an unfinished task now: execution state, next action,
 * priority and any pending ownership transfer. */
import { since } from '../../lib/room';
import type { Task } from '../../lib/types';
import { displayName, type Agents } from '../shared';

export function ExecutionFacts({
  task,
  execution,
  agents,
  now,
  busy,
  onAccept,
}: {
  task: Task;
  execution: { tone: string; text: string } | null;
  agents: Agents;
  now: number;
  busy: boolean;
  onAccept: () => void;
}) {
  if (task.status === 'done' || !(execution || task.next_action || task.priority || task.transfer)) return null;
  return (
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
              <button type="button" className="bd-btn" disabled={busy} onClick={onAccept}>
                Accept for {displayName(task.transfer.to, agents)}
              </button>
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}
