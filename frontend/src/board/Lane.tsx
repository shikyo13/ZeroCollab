/* One swimlane: a status column per task state, with the done column trimmed to recent cards. */
import { TASK_STATUSES } from '../lib/types';
import type { Lane as LaneData } from './lanes';
import { STATUS_LABELS, type Agents } from './shared';
import { TaskCard, type TaskFacts } from './TaskCard';
import type { CardFollow } from '../follow/Follow';

const DONE_PREVIEW = 8;

export function Lane({
  lane,
  plain,
  facts,
  agents,
  now,
  selected,
  onOpen,
  showAllDone,
  onToggleDone,
  follows,
}: {
  lane: LaneData;
  /** Status-only grouping: one unlabelled lane. */
  plain: boolean;
  facts: Map<string, TaskFacts>;
  agents: Agents;
  now: number;
  selected: string | null;
  onOpen: (id: string) => void;
  showAllDone: boolean;
  onToggleDone: () => void;
  /** Follow mode overlays by task id; absent when not following. */
  follows?: Map<string, CardFollow>;
}) {
  return (
    <section className={plain ? 'bd-lane-plain' : 'bd-lane'} aria-label={lane.title || undefined}>
      {!plain && (
        <h2 className="bd-lane-head">
          {lane.title} <span className="bd-count">{lane.total}</span>
        </h2>
      )}
      <div className="bd-columns">
        {TASK_STATUSES.map(status => {
          const list = lane.columns.get(status) ?? [];
          const shown = status === 'done' && !showAllDone ? list.slice(0, DONE_PREVIEW) : list;
          const headId = `bd-col-${lane.key || 'none'}-${status}`;
          return (
            <section key={status} className={`bd-column bd-column-${status}`} aria-labelledby={headId}>
              <h3 id={headId} className="bd-column-head">
                {STATUS_LABELS[status]} <span key={list.length} className="bd-count bd-count-roll">{list.length}</span>
              </h3>
              <div className="bd-column-body">
                {shown.map(task => (
                  <TaskCard key={task.id} task={task} facts={facts.get(task.id)!} agents={agents} now={now} selected={task.id === selected} onOpen={onOpen} follow={follows?.get(task.id)} />
                ))}
                {!list.length && <p className="bd-column-empty">Nothing {STATUS_LABELS[status].toLowerCase()}</p>}
                {status === 'done' && list.length > DONE_PREVIEW && (
                  <button type="button" className="bd-btn bd-btn-quiet" onClick={onToggleDone}>
                    {showAllDone ? 'Show recent only' : `Show all ${list.length}`}
                  </button>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
