/* Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory
 * and execution controls. Replaces the legacy static/project.js panel and uses
 * the same /api/runtime endpoints and project_state/runtime_state events. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { sessionTitle, useActiveChannel, useNow, useProjectState, useRuntimeState } from '../lib/room';
import type { Task } from '../lib/types';
import { USER } from '../lib/constants';
import { installAnnotateHook } from '../annotate';
import { ActivityFeed } from './ActivityFeed';
import { BoardToolbar } from './BoardToolbar';
import { CoordinationView, isOpenFollowup, isOpenReview } from './Coordination';
import { Lane } from './Lane';
import { buildLanes, type TaskFilters } from './lanes';
import { NewTaskForm } from './NewTaskForm';
import { LimitsPanel, MemoryPanel, SpecPanel } from './ProjectPanels';
import { ErrorText, type Agents } from './shared';
import { isStale, waitInfo, type TaskFacts } from './TaskCard';
import { TaskDetail } from './TaskDetail';
import { useBoardPrefs, type Tab } from './useBoardPrefs';
import { useChangeHighlights } from './useChangeHighlights';
import './board.css';

// The legacy chat lightbox can open the same annotation tool through window.ZeroAnnotate.
installAnnotateHook();

const NO_FILTERS: TaskFilters = { query: '', owner: '', label: '' };

export default function Board() {
  const { data: project, error: projectError, refresh } = useProjectState();
  const { data: runtimeState, error: runtimeError } = useRuntimeState();
  const channel = useActiveChannel();
  const now = useNow();
  const { tab, setTab, groupBy, setGroupBy, sortBy, setSortBy, feedOpen, setFeedOpen } = useBoardPrefs();
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get('task'));
  const [creating, setCreating] = useState(false);
  const [filters, setFilters] = useState<TaskFilters>(NO_FILTERS);
  const [showAllDone, setShowAllDone] = useState(false);
  const changed = useChangeHighlights(project?.tasks);
  const rootRef = useRef<HTMLDivElement>(null);

  // Opening a task from another section shows the task list without changing the remembered tab.
  const openTask = useCallback((id: string) => {
    setSelected(id);
    setTab('tasks', false);
  }, [setTab]);

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      // Don't discard a half-typed check-in or edit: Esc only closes from outside form fields.
      const field = (e.target as HTMLElement | null)?.closest?.('input, textarea, select');
      // The shell keeps the Board mounted while hidden; ignore Esc unless the task list is on screen.
      const visible = tab === 'tasks' && !!rootRef.current?.offsetParent;
      if (e.key === 'Escape' && !e.defaultPrevented && !field && visible) setSelected(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, tab]);

  const agents: Agents = useMemo(() => runtimeState?.agents ?? {}, [runtimeState]);
  const runs = useMemo(() => runtimeState?.runs ?? [], [runtimeState]);

  const facts = useMemo(() => {
    const map = new Map<string, TaskFacts>();
    if (!project) return map;
    const byId = new Map(project.tasks.map(t => [t.id, t]));
    const active = new Set(runs.map(r => r.agent));
    for (const task of project.tasks) {
      map.set(task.id, {
        stale: isStale(task, now, project.stale_seconds),
        blockedBy: (task.depends_on ?? []).map(id => byId.get(id)).filter((t): t is Task => !!t && t.status !== 'done'),
        openReviews: (project.reviews ?? []).filter(r => r.task_id === task.id && isOpenReview(r)),
        openFollowups: (project.followups ?? []).filter(f => f.task_id === task.id && isOpenFollowup(f)),
        ownerActive: !!task.owner && active.has(task.owner),
        changed: changed.has(task.id),
        wait: waitInfo(task, project),
      });
    }
    return map;
  }, [project, now, runs, changed]);

  const lanes = useMemo(
    () => (project ? buildLanes(project.tasks, filters, groupBy, sortBy, agents) : []),
    [project, filters, groupBy, sortBy, agents],
  );

  if (!project) {
    return (
      <div className="bd-root">
        {projectError ? <ErrorText message={`Could not load the board: ${projectError}`} /> : <p className="bd-empty">Loading board...</p>}
      </div>
    );
  }

  const selectedTask = selected ? project.tasks.find(t => t.id === selected) : undefined;
  const active = project.tasks.filter(t => t.status !== 'done').length;
  const openCoordination =
    (project.reviews ?? []).filter(isOpenReview).length + (project.followups ?? []).filter(isOpenFollowup).length;
  const waitingOnUser =
    (project.reviews ?? []).filter(r => r.reviewer === USER && r.status === 'pending').length +
    (project.followups ?? []).filter(f => f.assigned_to === USER && f.status === 'open').length;
  const owners = [...new Set(project.tasks.map(t => t.owner).filter(Boolean))];
  const knownLabels = [...new Set(project.tasks.flatMap(t => t.labels ?? []))].sort();
  const side = selectedTask ? 'detail' : feedOpen ? 'feed' : '';

  const tabs: [Tab, string, string?][] = [
    ['tasks', 'Tasks', active ? String(active) : undefined],
    ['coordination', 'Reviews & follow-ups', openCoordination ? String(openCoordination) : undefined],
    ['spec', 'Spec', project.spec ? undefined : 'none'],
    ['memory', 'Memory'],
    ['controls', 'Limits & usage'],
  ];

  return (
    <div className="bd-root" ref={rootRef}>
      <header className="bd-header">
        <div>
          <h1 className="bd-title">Board</h1>
          <p className="bd-muted">
            {active} active {active === 1 ? 'task' : 'tasks'} · {openCoordination} open reviews and follow-ups · {sessionTitle(channel)}
            {waitingOnUser > 0 && (
              <>
                {' · '}
                <button type="button" className="bd-link bd-attention" onClick={() => setTab('coordination')}>
                  {waitingOnUser} waiting on you
                </button>
              </>
            )}
          </p>
        </div>
        <nav className="bd-tabs" aria-label="Board sections">
          {tabs.map(([id, label, badge]) => (
            <button
              key={id}
              type="button"
              className="bd-tab"
              aria-current={tab === id ? 'page' : undefined}
              onClick={() => setTab(id)}
            >
              {label}
              {badge && <span className="bd-badge">{badge}</span>}
            </button>
          ))}
        </nav>
      </header>
      <ErrorText message={projectError || runtimeError} />

      {/* Every section stays mounted (hidden when inactive) so unsaved spec, limits,
          check-in and review drafts survive tab switches, as they did in project.js. */}
      <div className={`bd-tasks${side ? ' bd-tasks-with-detail' : ''}`} hidden={tab !== 'tasks'}>
        <div className="bd-tasks-main">
          <BoardToolbar
            filters={filters}
            onFilters={setFilters}
            owners={owners}
            labels={knownLabels}
            agents={agents}
            groupBy={groupBy}
            onGroupBy={setGroupBy}
            sortBy={sortBy}
            onSortBy={setSortBy}
            feedShown={side === 'feed'}
            onToggleFeed={() => {
              setFeedOpen(!feedOpen || !!selectedTask);
              setSelected(null);
            }}
            creating={creating}
            onToggleCreate={() => setCreating(v => !v)}
          />
          {creating && (
            <section className="bd-panel" aria-label="New task">
              <NewTaskForm
                agents={agents}
                tasks={project.tasks}
                channel={channel}
                onCancel={() => setCreating(false)}
                onCreated={task => {
                  setCreating(false);
                  setSelected(task.id);
                  void refresh();
                }}
              />
            </section>
          )}
          {project.tasks.length === 0 && !creating && (
            <div className="bd-panel bd-empty-state">
              <h3>No tasks yet</h3>
              <p>
                Tasks give one owner a concrete outcome and reserve the files they will edit, so agents don't collide. Agents create
                and claim tasks themselves as they coordinate; you can also add one here.
              </p>
              <button type="button" className="bd-btn bd-btn-primary" onClick={() => setCreating(true)}>
                Create the first task
              </button>
            </div>
          )}
          {project.tasks.length > 0 && !lanes.length && (
            <div className="bd-panel bd-empty-state">
              <p>No tasks match these filters.</p>
              <button type="button" className="bd-btn" onClick={() => setFilters(NO_FILTERS)}>
                Clear filters
              </button>
            </div>
          )}
          {project.tasks.length > 0 &&
            lanes.map(lane => (
              <Lane
                key={lane.key}
                lane={lane}
                plain={groupBy === 'status'}
                facts={facts}
                agents={agents}
                now={now}
                selected={selected}
                onOpen={id => setSelected(cur => (cur === id ? null : id))}
                showAllDone={showAllDone}
                onToggleDone={() => setShowAllDone(v => !v)}
              />
            ))}
        </div>
        {selectedTask && (
          <TaskDetail
            task={selectedTask}
            project={project}
            agents={agents}
            channel={channel}
            now={now}
            onClose={() => setSelected(null)}
            onOpenTask={openTask}
          />
        )}
        {side === 'feed' && (
          <ActivityFeed project={project} agents={agents} runs={runs} now={now} onOpenTask={openTask} onClose={() => setFeedOpen(false)} />
        )}
      </div>
      <div hidden={tab !== 'coordination'}>
        <CoordinationView project={project} agents={agents} channel={channel} onOpenTask={openTask} />
      </div>
      <div hidden={tab !== 'spec'}>
        <SpecPanel project={project} channel={channel} refresh={refresh} />
      </div>
      <div hidden={tab !== 'memory'}>
        <MemoryPanel project={project} agents={agents} />
      </div>
      <div className="bd-stack" hidden={tab !== 'controls'}>
        <LimitsPanel project={project} channel={channel} refresh={refresh} />
      </div>
    </div>
  );
}
