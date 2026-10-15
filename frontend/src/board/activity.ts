/* Presentation derived from task-level activity and explicit coordination records. */
import type { ProjectState, Task } from '../lib/types';
import { displayName, type Agents } from './shared';

export const ACTIVITY_GROUPS = [
  ['active', 'Active'], ['review', 'In review'], ['waiting', 'Waiting'],
  ['planned', 'Planned'], ['later', 'Later'], ['done', 'Done'],
] as const;
export type ActivityGroup = typeof ACTIVITY_GROUPS[number][0];
export interface TaskActivity {
  group: ActivityGroup;
  state: string;
  reasons: string[];
}

/** Completed references do not keep a task waiting. Missing records stay explicit. */
export function taskActivity(task: Task, project: ProjectState, agents: Agents): TaskActivity {
  const waits = new Set<string>();
  const reviewReasons = new Set<string>();
  const addTask = (id: string) => {
    const target = project.tasks.find(t => t.id === id);
    if (!target) waits.add(`Waiting details unavailable · task ${id}`);
    else if (target.status !== 'done') waits.add(`Waiting for task · ${target.title}`);
  };
  const addReview = (id: string) => {
    const target = project.reviews?.find(r => r.id === id);
    if (!target) waits.add(`Waiting details unavailable · review ${id}`);
    else if (target.status === 'pending') reviewReasons.add(`Awaiting review · ${displayName(target.reviewer, agents)}`);
  };
  const addFollowup = (id: string) => {
    const target = project.followups?.find(f => f.id === id);
    if (!target) waits.add(`Waiting details unavailable · follow-up ${id}`);
    else if (target.status === 'open') waits.add(`Waiting for ${displayName(target.assigned_to, agents)} · ${target.text.split('\n')[0]}`);
  };
  for (const id of task.depends_on ?? []) addTask(id);
  if (task.wait) (task.wait.kind === 'review' ? addReview : addFollowup)(task.wait.record_id);
  for (const ref of task.wait_on ?? []) {
    if ('task' in ref) addTask(ref.task);
    else if ('review' in ref) addReview(ref.review);
    else if ('followup' in ref) addFollowup(ref.followup);
    // The project snapshot has no release completion record. Show the recorded wait,
    // never infer installation from a version mentioned in next_action.
    else waits.add(`Waiting for release · ${ref.release}`);
  }
  const reviews = (project.reviews ?? []).filter(r => r.task_id === task.id && r.status === 'pending');
  for (const review of reviews) addReview(review.id);
  const reasons = [...waits, ...reviewReasons];
  if (task.status === 'done') return { group: 'done', state: 'Done', reasons: [] };
  if (task.execution_state === 'executing' || task.execution_state === 'queued')
    return { group: 'active', state: task.execution_state === 'executing' ? 'Running now' : 'Queued to start', reasons };
  if (task.next_action_state === 'deferred') return { group: 'later', state: 'Deferred', reasons };
  if (waits.size) return { group: 'waiting', state: reasons[0], reasons: reasons.slice(1) };
  if (reviewReasons.size) return { group: 'review', state: reasons[0], reasons: reasons.slice(1) };
  const planned = task.status === 'open' && task.next_action_state !== 'waiting'
    && (!task.execution_state || task.execution_state === 'idle' || task.execution_state === 'unassigned');
  return { group: planned ? 'planned' : 'waiting', state: 'Next step not running', reasons: [] };
}
