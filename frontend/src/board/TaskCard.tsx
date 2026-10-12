import { useEffect, useRef } from 'react';
import type { Followup, ProjectState, Review, Task } from '../lib/types';
import { CardFollowLayer, followName, type CardFollow } from '../follow/Follow';
import { CardVideos, CardThumbnails } from './Attachments';
import { AgentChip, LabelChips, RelativeTime, displayName, type Agents } from './shared';
import { USER } from '../lib/constants';

export interface TaskFacts {
  stale: boolean;
  blockedBy: Task[];
  openReviews: Review[];
  openFollowups: Followup[];
  /** The owner is running a turn right now; it may be on other work. */
  ownerActive: boolean;
  /** Changed since the board last rendered it; briefly highlighted. */
  changed: boolean;
  /** The request to the user this task waits on, when it links one. */
  wait?: WaitInfo;
  previewColor?: string;
}

/** The request to the user that a task waits on, and what has happened to it. */
export interface WaitInfo {
  text?: string;
  outcome: 'pending' | 'answered' | 'cancelled' | 'missing';
}

// First line of the awaited request shown on the card.
const SUMMARY_CHARS = 140;

export function waitInfo(task: Task, project: ProjectState): WaitInfo | undefined {
  if (!task.wait) return undefined;
  const id = task.wait.record_id;
  let text: string | undefined;
  let outcome: WaitInfo['outcome'] = 'missing';
  if (task.wait.kind === 'followup') {
    const f = project.followups?.find(x => x.id === id);
    if (f) {
      text = f.text;
      outcome = f.status === 'open' ? 'pending' : f.status === 'resolved' ? 'answered' : 'cancelled';
    }
  } else {
    const r = project.reviews?.find(x => x.id === id);
    if (r) {
      text = r.request;
      outcome = r.status === 'pending' ? 'pending' : r.status === 'approved' || r.status === 'changes_requested' ? 'answered' : 'cancelled';
    }
  }
  const line = text?.split('\n')[0].trim();
  return { outcome, text: line && line.length > SUMMARY_CHARS ? line.slice(0, SUMMARY_CHARS - 1) + '…' : line };
}

const WAIT_ENDED: Record<WaitInfo['outcome'], string> = {
  answered: 'Your answer is in; owner should act',
  cancelled: 'Request to you was cancelled; owner should check the task',
  missing: 'Wait ended or changed; owner should check the task',
  pending: 'Wait ended or changed; owner should check the task',
};

/** What is actually happening, from the server's execution state; null when the lane already says it. */
export function executionLine(task: Task, wait?: WaitInfo): { tone: string; text: string } | null {
  switch (task.execution_state) {
    case 'executing':
      return { tone: 'run', text: 'Running now' };
    case 'queued':
      return { tone: 'queued', text: 'Queued to start' };
    case 'waiting_on_human':
      return { tone: 'you', text: wait?.text ? `Waiting on you: ${wait.text}` : 'Waiting on you' };
    case 'needs_attention':
      return { tone: 'attention', text: WAIT_ENDED[wait?.outcome ?? 'missing'] };
    case 'idle':
      return task.status === 'working' || task.status === 'blocked'
        ? { tone: 'idle', text: `${task.status === 'working' ? 'Working task' : 'Blocked task'} · no turn running` }
        : null;
    default:
      return null;
  }
}

/** Latest check-in with real text; lifecycle-only entries are history, not progress. */
export function lastCheckin(task: Task) {
  for (let i = task.updates.length - 1; i >= 0; i--) if (!task.updates[i].auto) return task.updates[i];
  return undefined;
}

export function isStale(task: Task, now: number, staleSeconds: number): boolean {
  // The server knows about waits on the user; older servers don't send it.
  if (task.stale !== undefined) return task.stale;
  return !!task.owner && (task.status === 'working' || task.status === 'blocked') && now - task.updated > staleSeconds;
}

export function TaskCard({
  task,
  facts,
  agents,
  now,
  selected,
  onOpen,
  follow,
}: {
  task: Task;
  facts: TaskFacts;
  agents: Agents;
  now: number;
  selected: boolean;
  onOpen: (id: string) => void;
  /** Follow mode's live agent and recent move for this card, when following. */
  follow?: CardFollow;
}) {
  const latest = lastCheckin(task);
  const changes = facts.openReviews.filter(r => r.status === 'changes_requested').length;
  const pending = facts.openReviews.length - changes;
  const execution = task.status === 'done' ? null : executionLine(task, facts.wait);
  const waiting = task.execution_state === 'waiting_on_human';
  const liveAgent = follow?.live?.agent;
  const cardRef = useRef<HTMLElement>(null);
  const liveRun = follow?.live?.run;
  useEffect(() => {
    if (!liveRun) return;
    cardRef.current?.scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }, [liveRun]);
  return (
    <article
      ref={cardRef}
      data-task-id={task.id}
      data-task-status={task.status}
      className={`bd-card${task.status === 'done' ? ' bd-card-done' : ''}${facts.previewColor ? ' bd-card-preview' : ''}${facts.stale ? ' bd-card-stale' : ''}${waiting ? ' bd-card-you' : ''}${selected ? ' bd-card-selected' : ''}${facts.changed ? ' bd-card-changed' : ''}${liveAgent ? ' fl-card-live' : ''}${follow?.move ? ' fl-card-moved' : ''}`}
      style={{ '--agent-color': liveAgent ? agents[liveAgent]?.color || 'var(--accent)' : undefined, '--preview-color': facts.previewColor } as React.CSSProperties}
    >
      {follow && <CardFollowLayer follow={follow} agents={agents} part="bubble" />}
      <button
        type="button"
        className="bd-card-open"
        onClick={() => onOpen(task.id)}
        aria-label={`Open task ${task.title}`}
        aria-expanded={selected}
      >
        <span className="bd-card-title">
          {task.priority && (
            <span className="bd-priority" title={`Priority ${task.priority} (1 is highest)`}>
              P{task.priority}
            </span>
          )}
          {task.title}
        </span>
      </button>
      <CardVideos attachments={task.attachments} />
      <div className="bd-card-meta">
        <AgentChip name={task.owner} agents={task.owner && agents[task.owner] ? {...agents, [task.owner]: {...agents[task.owner], label: followName(task.owner, agents)}} : agents} />
        <code className="bd-id">{task.id}</code>
      </div>
      <div className="bd-card-content">
      {execution && (
        <p className={`bd-exec bd-exec-${execution.tone}`}>
          <span className="bd-exec-dot" aria-hidden="true" />
          {execution.text}
        </p>
      )}
      {follow && <CardFollowLayer follow={follow} agents={agents} part="body" />}
      {task.next_action && task.status !== 'done' && (
        <p className="bd-next">
          <span className="bd-muted">{waiting ? 'Then:' : 'Next:'}</span> {task.next_action}
        </p>
      )}
      {facts.ownerActive && task.status !== 'done' && task.execution_state !== 'executing' && (
        <p className="bd-active" title={`${displayName(task.owner, agents)} is running a turn now, possibly on other work. Task status changes only when they update it.`}>
          <span className="bd-active-dot" aria-hidden="true" />
          Owner active
        </p>
      )}
      <LabelChips labels={task.labels} />
      <CardThumbnails attachments={task.attachments} />
      {task.paths.length > 0 && (
        <ul className="bd-paths" aria-label="Reserved paths">
          {task.paths.slice(0, 3).map(path => (
            <li key={path}>
              <code>{path}</code>
            </li>
          ))}
          {task.paths.length > 3 && <li className="bd-more">+{task.paths.length - 3} more</li>}
        </ul>
      )}
      {(facts.blockedBy.length > 0 || pending > 0 || changes > 0 || facts.openFollowups.length > 0 || facts.stale || task.transfer) && (
        <ul className="bd-flags">
          {task.transfer && (
            <li className="bd-flag bd-flag-info" title={`${displayName(task.owner, agents)} keeps it until ${displayName(task.transfer.to, agents)} accepts`}>
              Transfer to {displayName(task.transfer.to, agents)} pending
            </li>
          )}
          {facts.stale && <li className="bd-flag bd-flag-warn">Check-in overdue</li>}
          {facts.blockedBy.length > 0 && (
            <li className="bd-flag bd-flag-warn" title={facts.blockedBy.map(t => t.title).join(', ')}>
              Waiting on {facts.blockedBy.length} {facts.blockedBy.length === 1 ? 'task' : 'tasks'}
            </li>
          )}
          {pending > 0 && <li className="bd-flag">{pending} review{pending === 1 ? '' : 's'} pending</li>}
          {changes > 0 && <li className="bd-flag bd-flag-danger">Changes requested</li>}
          {facts.openFollowups.length > 0 && (
            <li className="bd-flag">
              {facts.openFollowups.length} follow-up{facts.openFollowups.length === 1 ? '' : 's'}
            </li>
          )}
        </ul>
      )}
      {latest && (
        <p className="bd-card-update">
          <strong>{latest.by === USER ? 'You' : latest.by}:</strong> {latest.text}
        </p>
      )}
      <p className="bd-card-age">
        <RelativeTime time={task.updated} now={now} prefix="Updated " />
      </p>
      </div>
    </article>
  );
}
