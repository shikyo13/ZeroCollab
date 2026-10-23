import { Component, createRef, useState, type ReactNode } from 'react';
import { DONE_PREVIEW } from './Lane';
import type { Task } from '../lib/types';
import type { CardFollow } from '../follow/Follow';
import { ACTIVITY_GROUPS, type TaskActivity } from './activity';
import type { TaskFacts } from './TaskCard';
import type { Agents } from './shared';

const CARD_CONTROLS = 'button, a[href], input, select, textarea, [tabindex]';
type FocusSnapshot = { element: HTMLElement; task: string; control: number } | null;

/** Capture before React removes a card from its old section, then restore only lost focus. */
class ActivityFocus extends Component<{ children: ReactNode; filterKey: string }> {
  private root = createRef<HTMLDivElement>();

  getSnapshotBeforeUpdate(): FocusSnapshot {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || !this.root.current?.contains(element)) return null;
    const card = element.closest<HTMLElement>('[data-task-id]');
    if (!card?.dataset.taskId) return null;
    return { element, task: card.dataset.taskId, control: [...card.querySelectorAll(CARD_CONTROLS)].indexOf(element) };
  }

  componentDidUpdate(previous: { children: ReactNode; filterKey: string }, _state: unknown, snapshot: FocusSnapshot) {
    if (this.props.filterKey && previous.filterKey !== this.props.filterKey)
      this.root.current?.querySelector('.bd-activity-row')?.scrollIntoView({ block: 'nearest' });
    if (!snapshot || snapshot.element.isConnected || document.activeElement !== document.body) return;
    const card = [...(this.root.current?.querySelectorAll<HTMLElement>('[data-task-id]') ?? [])]
      .find(node => node.dataset.taskId === snapshot.task);
    const control = card?.querySelectorAll<HTMLElement>(CARD_CONTROLS)[snapshot.control]
      ?? card?.querySelector<HTMLElement>('.bd-activity-row');
    control?.focus({ preventScroll: true });
  }

  render() {
    return <div ref={this.root} className="bd-activity-sections">{this.props.children}</div>;
  }
}

export function ActivitySections({ tasks, activity, selected, onOpen, showAllDone, onToggleDone, filterKey }: {
  filterKey: string;
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
  const [collapsed, setCollapsed] = useState<{ filterKey: string; groups: Set<string> }>(() => ({ filterKey, groups: new Set() }));
  return <ActivityFocus filterKey={filterKey}>
    {ACTIVITY_GROUPS.map(([key, title]) => {
      const list = tasks.filter(task => activity.get(task.id)?.group === key);
      if (filterKey && !list.length) return null;
      const shown = key === 'done' && !showAllDone ? list.slice(0, DONE_PREVIEW) : list;
      const closed = collapsed.filterKey === filterKey && collapsed.groups.has(key);
      return <section key={key} className={`bd-activity-section bd-activity-${key}`} aria-labelledby={`bd-activity-${key}`}>
        <h2 id={`bd-activity-${key}`} className="bd-lane-head">
          <button type="button" className="bd-activity-heading" aria-expanded={!closed} aria-controls={`bd-activity-list-${key}`}
            onClick={() => setCollapsed(previous => {
              const next = new Set(previous.filterKey === filterKey ? previous.groups : []);
              if (next.has(key)) next.delete(key); else next.add(key);
              return { filterKey, groups: next };
            })}>
            <span aria-hidden="true">{closed ? '▸' : '▾'}</span>{title}<span className="bd-count">{list.length}</span>
          </button>
        </h2>
        <div id={`bd-activity-list-${key}`} hidden={closed}>
          {shown.map(task => {
            const state = activity.get(task.id)?.state ?? '';
            const shortState = state.startsWith('Waiting for task') ? 'Waiting on task'
              : state.startsWith('Waiting details unavailable') ? 'Wait unspecified'
              : state === 'Next step not running' ? (key === 'planned' ? 'Planned' : 'Not running')
              : state.split(' · ')[0];
            return <div key={task.id} data-task-id={task.id}>
              <button type="button" className={`bd-activity-row${selected === task.id ? ' is-selected' : ''}`}
                aria-pressed={selected === task.id} onClick={() => onOpen(task.id)}>
                <span className={`bd-row-priority bd-row-priority-${task.priority ?? 2}`} title={`Priority ${task.priority ?? 2}`}>P{task.priority ?? 2}</span>
                <span className="bd-row-title" title={task.title}>{task.title}</span>
                <span className="bd-row-owner" title={task.owner || 'Unclaimed'}>{task.owner || 'Unclaimed'}</span>
                <span className="bd-row-state" title={state}>{shortState}</span>
              </button>
            </div>;
          })}
          {key === 'done' && list.length > DONE_PREVIEW && <button type="button" className="bd-btn bd-btn-quiet" onClick={onToggleDone}>
            {showAllDone ? 'Show recent only' : `Show all ${list.length}`}
          </button>}
        </div>
      </section>;
    })}
  </ActivityFocus>;
}
