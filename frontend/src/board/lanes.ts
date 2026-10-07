/* Filtering, swimlane grouping and column ordering for the task board. */
import { TASK_STATUSES, type Task, type TaskStatus } from '../lib/types';
import { displayName, type Agents } from './shared';
import type { GroupBy, SortBy } from './useBoardPrefs';

export interface Lane {
  key: string;
  title: string;
  columns: Map<TaskStatus, Task[]>;
  total: number;
}

/** Owner and area filters: '' matches everything, '-' matches tasks without one. */
export interface TaskFilters {
  query: string;
  owner: string;
  label: string;
}

const SORTERS: Record<SortBy, (a: Task, b: Task) => number> = {
  recent: (a, b) => b.updated - a.updated,
  oldest: (a, b) => a.updated - b.updated,
  title: (a, b) => a.title.localeCompare(b.title),
};

function matches(task: Task, { query, owner, label }: TaskFilters): boolean {
  if (owner === '-' ? task.owner : owner && task.owner !== owner) return false;
  if (label === '-' ? task.labels?.length : label && !task.labels?.includes(label)) return false;
  const q = query.trim().toLowerCase();
  const haystack = `${task.title} ${task.details} ${task.id} ${task.paths.join(' ')} ${task.owner} ${(task.labels ?? []).join(' ')}`;
  return !q || haystack.toLowerCase().includes(q);
}

export function buildLanes(tasks: Task[], filters: TaskFilters, groupBy: GroupBy, sortBy: SortBy, agents: Agents): Lane[] {
  // Swimlanes: a task sits in the lane of its first area, or of its owner.
  const keyOf = (task: Task) => (groupBy === 'area' ? task.labels?.[0] ?? '' : groupBy === 'owner' ? task.owner : 'all');
  const titleOf = (key: string) => (groupBy === 'area' ? key || 'No area' : groupBy === 'owner' ? displayName(key, agents) : '');
  const grouped = new Map<string, Task[]>();
  for (const task of tasks) {
    if (!matches(task, filters)) continue;
    const key = keyOf(task);
    const list = grouped.get(key);
    if (list) list.push(task);
    else grouped.set(key, [task]);
  }
  const keys = [...grouped.keys()].sort((a, b) => (!a ? 1 : !b ? -1 : titleOf(a).localeCompare(titleOf(b))));
  return keys.map(key => {
    const columns = new Map<TaskStatus, Task[]>(TASK_STATUSES.map(s => [s, []]));
    const list = grouped.get(key) ?? [];
    for (const task of list) columns.get(task.status)?.push(task);
    for (const column of columns.values()) column.sort(SORTERS[sortBy]);
    return { key, title: titleOf(key), columns, total: list.length };
  });
}
