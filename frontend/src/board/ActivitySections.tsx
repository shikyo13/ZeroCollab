import { Component, createRef, type ReactNode } from 'react';
import { DONE_PREVIEW } from './Lane';
import type { Task } from '../lib/types';
import type { CardFollow } from '../follow/Follow';
import { ACTIVITY_GROUPS, type TaskActivity } from './activity';
import { TaskCard, type TaskFacts } from './TaskCard';
import type { Agents } from './shared';

const CARD_CONTROLS = 'button, a[href], input, select, textarea, [tabindex]';
type FocusSnapshot = { element: HTMLElement; task: string; control: number } | null;

/** Capture before React removes a card from its old section, then restore only lost focus. */
class ActivityFocus extends Component<{ children: ReactNode }> {
  private root = createRef<HTMLDivElement>();

  getSnapshotBeforeUpdate(): FocusSnapshot {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || !this.root.current?.contains(element)) return null;
    const card = element.closest<HTMLElement>('[data-task-id]');
    if (!card?.dataset.taskId) return null;
    return { element, task: card.dataset.taskId, control: [...card.querySelectorAll(CARD_CONTROLS)].indexOf(element) };
  }

  componentDidUpdate(_props: { children: ReactNode }, _state: unknown, snapshot: FocusSnapshot) {
    if (!snapshot || snapshot.element.isConnected || document.activeElement !== document.body) return;
    const card = [...(this.root.current?.querySelectorAll<HTMLElement>('[data-task-id]') ?? [])]
      .find(node => node.dataset.taskId === snapshot.task);
    const control = card?.querySelectorAll<HTMLElement>(CARD_CONTROLS)[snapshot.control]
      ?? card?.querySelector<HTMLElement>('.bd-card-open');
    control?.focus({ preventScroll: true });
  }

  render() {
    return <div ref={this.root} className="bd-activity-sections">{this.props.children}</div>;
  }
}

export function ActivitySections({ tasks, activity, facts, agents, now, selected, onOpen, follows, showAllDone, onToggleDone }: {
  tasks: Task[];
  showAllDone: boolean;
  onToggleDone: () => void;
  activity: Map<string, TaskActivity>;
  facts: Map<string, TaskFacts>;
  agents: Agents;
  now: number;
  selected: string | null;
  onOpen: (id: string) => void;
  follows?: Map<string, CardFollow>;
}) {
  return <ActivityFocus>
    {ACTIVITY_GROUPS.map(([key, title]) => {
      const list = tasks.filter(task => activity.get(task.id)?.group === key);
      const shown = key === 'done' && !showAllDone ? list.slice(0, DONE_PREVIEW) : list;
      return <section key={key} className={`bd-activity-section bd-activity-${key}`} aria-labelledby={`bd-activity-${key}`}>
        <h2 id={`bd-activity-${key}`} className="bd-lane-head">
          {title} <span className="bd-count">{list.length}</span>
        </h2>
        <div className="bd-activity-grid">
          {shown.map(task => <TaskCard key={task.id} task={task} activity={activity.get(task.id)} facts={facts.get(task.id)!}
            agents={agents} now={now} selected={selected === task.id} onOpen={onOpen} follow={follows?.get(task.id)} />)}
        </div>
        {key === 'done' && list.length > DONE_PREVIEW && <button type="button" className="bd-btn bd-btn-quiet" onClick={onToggleDone}>
          {showAllDone ? 'Show recent only' : `Show all ${list.length}`}
        </button>}
        {!list.length && <p className="bd-muted">No tasks in this group</p>}
      </section>;
    })}
  </ActivityFocus>;
}
