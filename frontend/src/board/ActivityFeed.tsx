import { sessionTitle } from '../lib/room';
/* Live team activity: who is running right now, then the newest board events
 * (check-ins, ownership/status moves, notes, unlinked reviews and follow-ups).
 * Task-linked reviews and follow-ups already log into task history, so only
 * unlinked records are read directly to avoid listing the same action twice. */
import { since } from '../lib/room';
import type { ProjectState, RunInfo } from '../lib/types';
import { AgentChip, REVIEW_LABELS, displayName, type Agents } from './shared';

const FEED_LIMIT = 50;

interface FeedItem {
  key: string;
  time: number;
  by: string;
  text: string;
  kind: 'event' | 'checkin' | 'note' | 'record';
  tag?: string;
  taskId?: string;
  taskTitle?: string;
}

function feedItems(project: ProjectState, agents: Agents): FeedItem[] {
  const items: FeedItem[] = [];
  for (const task of project.tasks) {
    task.updates.forEach((u, i) =>
      items.push({
        key: `t:${task.id}:${u.time}:${i}`,
        time: u.time,
        by: u.by,
        text: u.auto ? u.event ?? u.text : u.text,
        kind: u.auto ? 'event' : 'checkin',
        tag: u.auto ? undefined : u.event,
        taskId: task.id,
        taskTitle: task.title,
      }),
    );
  }
  project.notes.forEach((n, i) => {
    if (n.time) items.push({ key: `n:${i}:${n.time}`, time: n.time, by: n.by, text: n.text, kind: 'note', tag: n.kind });
  });
  for (const r of project.reviews ?? []) {
    if (r.task_id) continue;
    const pending = r.status === 'pending';
    items.push({
      key: `r:${r.id}:${r.updated}`,
      time: r.updated,
      by: pending || r.status === 'cancelled' ? r.requested_by : r.reviewer,
      text: pending ? `requested a review from ${displayName(r.reviewer, agents)}: ${r.request}` : r.findings || r.cancellation || r.request,
      kind: 'record',
      tag: pending ? 'review' : REVIEW_LABELS[r.status].toLowerCase(),
    });
  }
  for (const f of project.followups ?? []) {
    if (f.task_id) continue;
    const open = f.status === 'open';
    items.push({
      key: `f:${f.id}:${f.updated}`,
      time: f.updated,
      by: open || f.status === 'cancelled' ? f.created_by : f.assigned_to,
      text: open ? `asked ${displayName(f.assigned_to, agents)}: ${f.text}` : f.result || f.text,
      kind: 'record',
      tag: open ? 'follow-up' : `follow-up ${f.status}`,
    });
  }
  return items.sort((a, b) => b.time - a.time).slice(0, FEED_LIMIT);
}

export function ActivityFeed({
  project,
  agents,
  runs,
  now,
  onOpenTask,
  onClose,
}: {
  project: ProjectState;
  agents: Agents;
  runs: RunInfo[];
  now: number;
  onOpenTask: (id: string) => void;
  onClose: () => void;
}) {
  const items = feedItems(project, agents);
  return (
    <aside className="bd-side bd-feed" aria-labelledby="bd-feed-title">
      <header className="bd-detail-head bd-feed-head">
        <h2 id="bd-feed-title">Team activity</h2>
        <button type="button" className="bd-btn bd-btn-quiet bd-close" onClick={onClose} aria-label="Hide team activity">
          ×
        </button>
      </header>
      <section className="bd-detail-section" aria-labelledby="bd-feed-now">
        <h3 id="bd-feed-now">Running now</h3>
        {runs.length ? (
          <ul className="bd-feed-now">
            {runs.map(r => (
              <li key={r.id}>
                <span className="bd-active-dot" aria-hidden="true" />
                <AgentChip name={r.agent} agents={agents} />
                <span className="bd-muted">in {sessionTitle(r.channel)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="bd-muted">No agent is running a turn.</p>
        )}
      </section>
      <section className="bd-detail-section" aria-labelledby="bd-feed-recent">
        <h3 id="bd-feed-recent">Recent</h3>
        {items.length ? (
          <ol className="bd-feed-list">
            {items.map(item => (
              <li key={item.key} className={`bd-feed-item bd-feed-${item.kind}${item.kind === 'note' ? ` bd-note-${item.tag}` : ''}`}>
                <div className="bd-history-head">
                  <AgentChip name={item.by} agents={agents} />
                  {item.tag && <span className="bd-feed-tag">{item.tag}</span>}
                  <span className="bd-muted" title={new Date(item.time * 1000).toLocaleString()}>
                    {since(item.time, now)}
                  </span>
                </div>
                <p>{item.text}</p>
                {item.taskId && (
                  <button type="button" className="bd-link bd-feed-task" onClick={() => onOpenTask(item.taskId!)}>
                    {item.taskTitle}
                  </button>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="bd-muted">Check-ins, ownership changes and decisions appear here as the team works.</p>
        )}
      </section>
    </aside>
  );
}
