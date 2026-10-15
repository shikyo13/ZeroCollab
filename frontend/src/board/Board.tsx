/* Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory
 * and execution controls. Replaces the legacy static/project.js panel and uses
 * the same /api/runtime endpoints and project_state/runtime_state events. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { sessionTitle, useActiveChannel, useNow, useProjectState, useRuntimeState } from '../lib/room';
import type { Task } from '../lib/types';
import { EVENTS, USER } from '../lib/constants';
import { installAnnotateHook } from '../annotate';
import { ActivityFeed } from './ActivityFeed';
import { BoardToolbar } from './BoardToolbar';
import { CoordinationView, isOpenFollowup, isOpenReview } from './Coordination';
import { Lane } from './Lane';
import { ActivitySections } from './ActivitySections';
import { ACTIVITY_GROUPS, taskActivity } from './activity';
import { buildLanes, visibleTasks, type TaskFilters } from './lanes';
import { NewTaskForm } from './NewTaskForm';
import { LimitsPanel, MemoryPanel, SpecPanel } from './ProjectPanels';
import { ErrorText, type Agents } from './shared';
import { isStale, waitInfo, type TaskFacts } from './TaskCard';
import { TaskDetail } from './TaskDetail';
import { useBoardPrefs, type Tab } from './useBoardPrefs';
import { useChangeHighlights } from './useChangeHighlights';
import { FollowBanner, placeFollow, useFollowKeys, useReconcileRuns } from '../follow/Follow';
import { useBoardMotion } from './useBoardMotion';
import { useFollow, type PipItem } from '../follow/followStore';
import { FollowPip } from '../follow/Pip';
import './board.css';
import Decisions from '../decisions/Decisions';

// The legacy chat lightbox can open the same annotation tool through window.ZeroAnnotate.
installAnnotateHook();

const NO_FILTERS: TaskFilters = { query: '', owner: '', label: '' };

export default function Board() {
  const { data: latestProject, error: projectError, refresh } = useProjectState();
  const { data: latestRuntime, error: runtimeError } = useRuntimeState();
  const channel = useActiveChannel();
  const liveNow = useNow();
  const follow = useFollow();
  const projectId = latestRuntime?.project_id || window.activeProjectId || '';
  const displayed = useRef({ project: latestProject, runtime: latestRuntime, now: liveNow, id: projectId });
  // Pause freezes this view, not the server or incoming events. Resume catches up atomically.
  if (!follow.following || !follow.paused || displayed.current.id !== projectId)
    displayed.current = { project: latestProject, runtime: latestRuntime, now: liveNow, id: projectId };
  const { project, runtime: runtimeState, now } = displayed.current;
  const { tab, setTab, groupBy, setGroupBy, sortBy, setSortBy, feedOpen, setFeedOpen } = useBoardPrefs();
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get('task'));
  const [creating, setCreating] = useState(false);
  const [filters, setFilters] = useState<TaskFilters>(NO_FILTERS);
  const [showAllDone, setShowAllDone] = useState(false);
  const changed = useChangeHighlights(project?.tasks);
  const rootRef = useRef<HTMLDivElement>(null);
  const mediaRef = useRef<HTMLDivElement>(null);
  const [previewItems, setPreviewItems] = useState<PipItem[]>([]);
  const previewCount = previewItems.length;
  const onPreviewsChange = useCallback((items: PipItem[]) => setPreviewItems(items), []);
  const [mediaWidth, setMediaWidth] = useState(0);
  useEffect(() => {
    const node = mediaRef.current; if (!node) return;
    const observer = new ResizeObserver(entries => setMediaWidth(entries[0].contentRect.width));
    observer.observe(node); return () => observer.disconnect();
  }, [!!project]);
  const motionNotice = useBoardMotion(rootRef, follow.paused, projectId, follow.following && tab === 'tasks');
  useEffect(() => {
    const sync = () => {if (new URLSearchParams(location.search).has('decision')) setTab('decisions', false);};
    sync(); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync);
  }, [setTab]);

  // Opening a task from another section shows the task list without changing the remembered tab.
  const openTask = useCallback((id: string) => {
    setSelected(id);
    setTab('tasks', false);
  }, [setTab]);
  useEffect(() => {
    const open = (event: Event) => {
      const { task, tab: next } = (event as CustomEvent<{ task?: string; tab?: 'coordination' }>).detail ?? {};
      if (task) openTask(task);
      else if (next) setTab(next, false);
    };
    window.addEventListener(EVENTS.openTask, open);
    return () => window.removeEventListener(EVENTS.openTask, open);
  }, [openTask, setTab]);

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      // Don't discard a half-typed check-in or edit: Esc only closes from outside form fields.
      const field = (e.target as HTMLElement | null)?.closest?.('input, textarea, select');
      // The shell keeps the Board mounted while hidden; ignore Esc unless the task list is on screen.
      const visible = tab === 'tasks' && !!rootRef.current?.offsetParent;
      if (e.key === 'Escape' && !e.defaultPrevented && !field && visible) {
        e.preventDefault();  // used: follow mode's Esc stays on
        setSelected(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, tab]);

  const agents: Agents = useMemo(() => runtimeState?.agents ?? {}, [runtimeState]);
  const runs = useMemo(() => runtimeState?.runs ?? [], [runtimeState]);
  // The shell keeps the Board mounted while hidden; its shortcuts only apply while it is on screen.
  useFollowKeys(follow.following, follow.paused, () => !!rootRef.current?.offsetParent);
  useReconcileRuns(runs);
  const placement = useMemo(() => placeFollow(follow, project?.tasks ?? []), [follow, project]);

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
        previewColor: follow.following && previewItems.some(item => item.task === task.id) ? agents[task.owner]?.color || 'var(--accent)' : undefined,
      });
    }
    return map;
  }, [project, now, runs, changed, previewItems, follow.following, agents]);

  const lanes = useMemo(
    () => (project ? buildLanes(project.tasks, filters, groupBy, sortBy, agents) : []),
    [project, filters, groupBy, sortBy, agents],
  );

  const activity = useMemo(() => new Map((project?.tasks ?? []).map(task =>
    [task.id, taskActivity(task, project!, agents)])), [project, agents]);
  const filteredTasks = useMemo(() => visibleTasks(project?.tasks ?? [], filters, sortBy), [project, filters, sortBy]);

  if (!project) {
    return (
      <div className="bd-root">
        {projectError ? <ErrorText message={`Could not load the board: ${projectError}`} /> : <p className="bd-empty">Loading board...</p>}
      </div>
    );
  }

  const selectedTask = selected ? project.tasks.find(t => t.id === selected) : undefined;
  const active = project.tasks.filter(t => t.status !== 'done').length;
  const unfinished = (groupBy === 'activity' ? filteredTasks : project.tasks).filter(t => t.status !== 'done').length;
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
    ['decisions', 'Decisions'],
    ['spec', 'Spec', project.spec ? undefined : 'none'],
    ['memory', 'Memory'],
    ['controls', 'Limits & usage'],
  ];

  return (
    <div className={`bd-root${follow.paused ? ' is-follow-paused' : ''}`} ref={rootRef}>
      <header className="bd-header">
        <div>
          <h1 className="bd-title">Board</h1>
          <p className="bd-muted">
            {unfinished} unfinished {unfinished === 1 ? 'task' : 'tasks'} · {openCoordination} open reviews and follow-ups · {sessionTitle(channel)}
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
          <FollowBanner unplaced={placement.unplaced} agents={agents} />
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
          {groupBy === 'activity' && <ul className="bd-activity-counts" aria-label="Activity group counts">
            {ACTIVITY_GROUPS.map(([key, title]) => <li key={key}>{title}: {filteredTasks.filter(task => activity.get(task.id)?.group === key).length}</li>)}
          </ul>}
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
          {motionNotice && <p className="bd-motion-notice" role="status">{motionNotice}</p>}
          <div ref={mediaRef} className={`bd-media-layout${previewCount && follow.following ? ' has-previews' : ''}${mediaWidth < 1180 ? ' is-media-sheet' : ''}`}>
          <div className="bd-media-board">
          {project.tasks.length > 0 && (groupBy === 'activity'
            ? <ActivitySections tasks={filteredTasks} activity={activity} showAllDone={showAllDone} onToggleDone={() => setShowAllDone(v => !v)} facts={facts} agents={agents} now={now}
                selected={selected} onOpen={id => setSelected(cur => cur === id ? null : id)}
                follows={follow.following ? placement.cards : undefined} />
            : lanes.map(lane => (
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
                follows={follow.following ? placement.cards : undefined}
              />
            )))}
          </div>
          <FollowPip key={projectId} projectId={projectId} agents={agents} tasks={project.tasks} onOpenTask={openTask} onPreviewsChange={onPreviewsChange} />
          </div>
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
      <div hidden={tab !== 'decisions'}>
        <Decisions key={window.activeProjectId || runtimeState?.project_id || runtimeState?.project || ''}
          project={window.activeProjectId || runtimeState?.project_id || runtimeState?.project || ''}
          active={tab === 'decisions'} channel={channel} agents={agents} runs={runs} onTask={openTask} />
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
