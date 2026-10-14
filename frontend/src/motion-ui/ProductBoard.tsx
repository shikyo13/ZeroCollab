import { Lane } from '../board/Lane';
import { TaskCard, type TaskFacts } from '../board/TaskCard';
import { buildLanes } from '../board/lanes';
import type { Agents } from '../board/shared';
import type { CardFollow } from '../follow/Follow';
import type { Task } from '../lib/types';
import type { UiData, UiLayer } from './types';

const noAction = () => undefined;

export function ProductBoard({ layer, data }: { layer: UiLayer; data: UiData }) {
  const agents: Agents = Object.fromEntries(data.agents.map(a => [a.id, {
    ...a, handle: a.id, state: 'ready', provider: '', template: '',
    archived: false, detail: '', paused: false, queued: 0,
  }]));
  const tasks: Task[] = data.tasks.map(t => ({
    ...t, owner: t.owner ?? '', details: '', paths: [], updates: [], labels: t.labels ?? [],
    updated: t.updated ?? data.epoch_seconds,
  }));
  const facts = new Map<string, TaskFacts>(tasks.map(t => [t.id, {
    stale: false, blockedBy: [], openReviews: [], openFollowups: [],
    ownerActive: t.execution_state === 'executing', changed: false,
  }]));
  const follows = new Map<string, CardFollow>((data.follow ?? []).map(f => [f.task, {
    live: { agent: f.agent, task: f.task, run: '', channel: 'Scripted recording', text: f.text ?? '',
      started: data.epoch_seconds, updated: data.epoch_seconds,
      tool: f.tool ? { name: '', label: f.tool, status: 'completed', time: data.epoch_seconds } : undefined },
    move: f.move ? { ...f.move, by: f.agent, time: data.epoch_seconds } : undefined,
  }]));
  const active = tasks.filter(t => t.status !== 'done').length;
  if (layer.component === 'task-card') {
    const task = tasks.find(t => t.id === layer.task);
    if (!task) throw new Error(`Scripted task ${layer.task} is absent`);
    return <div className="bd-root"><TaskCard task={task} facts={facts.get(task.id)!} agents={agents}
      now={data.epoch_seconds} selected={false} onOpen={noAction} follow={follows.get(task.id)} /></div>;
  }
  const lanes = buildLanes(tasks, { query: '', owner: '', label: '' }, 'status', 'recent', agents);
  return <div className="bd-root">
    <header className="bd-header"><div><h1 className="bd-title">Board</h1>
      <p className="bd-muted">{active} active {active === 1 ? 'task' : 'tasks'} · {data.project_title ?? 'Scripted project'}</p>
    </div></header>
    {lanes.map(lane => <Lane key={lane.key} lane={lane} plain facts={facts} agents={agents}
      now={data.epoch_seconds} selected={null} onOpen={noAction} showAllDone onToggleDone={noAction} follows={follows} />)}
  </div>;
}
