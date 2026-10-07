/* Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory
 * and execution controls. Replaces the legacy static/project.js panel and uses
 * the same /api/runtime endpoints and project_state/runtime_state events. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { sessionTitle, useActiveChannel, useNow, useProjectState, useRuntimeState } from '../lib/room';
import { readStored, writeStored } from '../lib/storage';
import { TASK_STATUSES, type Task, type TaskStatus } from '../lib/types';
import { installAnnotateHook } from '../annotate';
import { ActivityFeed } from './ActivityFeed';
import { CoordinationView, isOpenFollowup, isOpenReview } from './Coordination';
import { NewTaskForm } from './NewTaskForm';
import { LimitsPanel, MemoryPanel, SpecPanel } from './ProjectPanels';
import { ErrorText, STATUS_LABELS, displayName, type Agents } from './shared';
import { TaskCard, isStale, waitInfo, type TaskFacts } from './TaskCard';
import { TaskDetail } from './TaskDetail';
import { STORAGE_KEYS, USER } from '../lib/constants';
import './board.css';

// The first entry of each list is the default when nothing valid is saved.
const TABS = ['tasks', 'coordination', 'spec', 'memory', 'controls'] as const;
const GROUPINGS = ['status', 'area', 'owner'] as const;
const SORTS = ['recent', 'oldest', 'title'] as const;
type Tab = typeof TABS[number];
type GroupBy = typeof GROUPINGS[number];
type SortBy = typeof SORTS[number];
const DONE_PREVIEW = 8;
// Wider than the board.css @media (max-width: 1100px) breakpoint, where the feed docks beside the columns.
const FEED_DOCKED_QUERY = '(min-width: 1101px)';

// The legacy chat lightbox can open the same annotation tool through window.ZeroAnnotate.
installAnnotateHook();
const CHANGE_HIGHLIGHT_MS = 2500;

function readChoice<T extends string>(key: string, allowed: readonly T[]): T {
  const saved = readStored(key) as T | null;
  return saved && allowed.includes(saved) ? saved : allowed[0];
}

const SORTERS: Record<SortBy, (a: Task, b: Task) => number> = {
  recent: (a, b) => b.updated - a.updated,
  oldest: (a, b) => a.updated - b.updated,
  title: (a, b) => a.title.localeCompare(b.title),
};

interface Lane {
  key: string;
  title: string;
  columns: Map<TaskStatus, Task[]>;
  total: number;
}

export default function Board() {
  const { data: project, error: projectError, refresh } = useProjectState();
  const { data: runtimeState, error: runtimeError } = useRuntimeState();
  const channel = useActiveChannel();
  const now = useNow();
  const [tab, setTabState] = useState<Tab>(() => readChoice(STORAGE_KEYS.boardTab, TABS));
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get('task'));
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [ownerFilter, setOwnerFilter] = useState('');
  const [labelFilter, setLabelFilter] = useState('');
  const [groupBy, setGroupByState] = useState<GroupBy>(() => readChoice(STORAGE_KEYS.boardGroup, GROUPINGS));
  const [sortBy, setSortByState] = useState<SortBy>(() => readChoice(STORAGE_KEYS.boardSort, SORTS));
  // The activity rail sits beside the columns on wide screens; on narrow ones it is an overlay, so it starts closed.
  const [feedOpen, setFeedOpenState] = useState(() => {
    const saved = readStored(STORAGE_KEYS.boardFeed);
    return saved ? saved === '1' : window.matchMedia(FEED_DOCKED_QUERY).matches;
  });
  const [showAllDone, setShowAllDone] = useState(false);
  const [changed, setChanged] = useState<ReadonlySet<string>>(new Set());
  const seenRef = useRef<Map<string, number> | null>(null);
  const mountedRef = useRef(true);
  const rootRef = useRef<HTMLDivElement>(null);

  const setTab = (next: Tab) => {
    setTabState(next);
    writeStored(STORAGE_KEYS.boardTab, next);
  };
  const setGroupBy = (next: GroupBy) => {
    setGroupByState(next);
    writeStored(STORAGE_KEYS.boardGroup, next);
  };
  const setSortBy = (next: SortBy) => {
    setSortByState(next);
    writeStored(STORAGE_KEYS.boardSort, next);
  };
  const setFeedOpen = (next: boolean) => {
    setFeedOpenState(next);
    writeStored(STORAGE_KEYS.boardFeed, next ? '1' : '0');
  };

  // Highlight cards that changed while the board is open. The first load only records a baseline.
  useEffect(() => {
    if (!project) return;
    const seen = seenRef.current;
    seenRef.current = new Map(project.tasks.map(t => [t.id, t.updated]));
    if (!seen) return;
    const fresh = project.tasks.filter(t => (seen.get(t.id) ?? 0) < t.updated).map(t => t.id);
    if (!fresh.length) return;
    setChanged(cur => new Set([...cur, ...fresh]));
    // Not cleared on the next update: back-to-back changes must each fade on their own schedule.
    window.setTimeout(() => {
      if (mountedRef.current) setChanged(cur => new Set([...cur].filter(id => !fresh.includes(id))));
    }, CHANGE_HIGHLIGHT_MS);
  }, [project]);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const openTask = useCallback((id: string) => {
    setSelected(id);
    setTabState('tasks');
  }, []);

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

  const lanes = useMemo((): Lane[] => {
    if (!project) return [];
    const q = query.trim().toLowerCase();
    const visible = project.tasks.filter(task => {
      if (ownerFilter === '-' ? task.owner : ownerFilter && task.owner !== ownerFilter) return false;
      if (labelFilter === '-' ? task.labels?.length : labelFilter && !task.labels?.includes(labelFilter)) return false;
      const haystack = `${task.title} ${task.details} ${task.id} ${task.paths.join(' ')} ${task.owner} ${(task.labels ?? []).join(' ')}`;
      return !q || haystack.toLowerCase().includes(q);
    });
    // Swimlanes: a task sits in the lane of its first area, or of its owner.
    const keyOf = (task: Task) => (groupBy === 'area' ? task.labels?.[0] ?? '' : groupBy === 'owner' ? task.owner : 'all');
    const titleOf = (key: string) =>
      groupBy === 'area' ? key || 'No area' : groupBy === 'owner' ? displayName(key, agents) : '';
    const grouped = new Map<string, Task[]>();
    for (const task of visible) grouped.set(keyOf(task), [...(grouped.get(keyOf(task)) ?? []), task]);
    const keys = [...grouped.keys()].sort((a, b) => (!a ? 1 : !b ? -1 : titleOf(a).localeCompare(titleOf(b))));
    return keys.map(key => {
      const columns = new Map<TaskStatus, Task[]>(TASK_STATUSES.map(s => [s, []]));
      for (const task of grouped.get(key) ?? []) columns.get(task.status)?.push(task);
      for (const list of columns.values()) list.sort(SORTERS[sortBy]);
      return { key, title: titleOf(key), columns, total: grouped.get(key)?.length ?? 0 };
    });
  }, [project, query, ownerFilter, labelFilter, groupBy, sortBy, agents]);

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
  const projectPath = runtimeState?.project ?? '';
  const projectName = projectPath.split(/[\\/]/).filter(Boolean).at(-1) ?? projectPath;
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
          {runtimeState && (
            <p className="bd-project" title="Each attached repository keeps its own board, tasks and history.">
              {projectPath ? (
                <>
                  Repository <strong title={projectPath}>{projectName}</strong>
                </>
              ) : (
                <>Workspace board - no repository attached</>
              )}
            </p>
          )}
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
            <div className="bd-toolbar">
              <input
                type="search"
                className="bd-search"
                placeholder="Filter by title, path, owner or ID"
                aria-label="Filter tasks"
                value={query}
                onChange={e => setQuery(e.target.value)}
              />
              <select aria-label="Filter by owner" value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
                <option value="">Everyone</option>
                <option value="-">Unclaimed</option>
                {owners.map(o => (
                  <option key={o} value={o}>
                    {agents[o]?.label || o}
                  </option>
                ))}
              </select>
              {knownLabels.length > 0 && (
                <select aria-label="Filter by area" value={labelFilter} onChange={e => setLabelFilter(e.target.value)}>
                  <option value="">All areas</option>
                  <option value="-">No area</option>
                  {knownLabels.map(label => (
                    <option key={label} value={label}>
                      {label}
                    </option>
                  ))}
                </select>
              )}
              <select aria-label="Group by" value={groupBy} onChange={e => setGroupBy(e.target.value as GroupBy)}>
                <option value="status">Group: status only</option>
                <option value="area">Group: area</option>
                <option value="owner">Group: owner</option>
              </select>
              <select aria-label="Sort cards" value={sortBy} onChange={e => setSortBy(e.target.value as SortBy)}>
                <option value="recent">Sort: recent activity</option>
                <option value="oldest">Sort: least recent</option>
                <option value="title">Sort: title</option>
              </select>
              <button
                type="button"
                className="bd-btn"
                aria-pressed={side === 'feed'}
                onClick={() => {
                  setFeedOpen(!feedOpen || !!selectedTask);
                  setSelected(null);
                }}
              >
                Team activity
              </button>
              <button type="button" className="bd-btn bd-btn-primary" onClick={() => setCreating(v => !v)} aria-expanded={creating}>
                {creating ? 'Close' : 'New task'}
              </button>
            </div>
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
                <button
                  type="button"
                  className="bd-btn"
                  onClick={() => {
                    setQuery('');
                    setOwnerFilter('');
                    setLabelFilter('');
                  }}
                >
                  Clear filters
                </button>
              </div>
            )}
            {project.tasks.length > 0 &&
              lanes.map(lane => (
                <section key={lane.key} className={groupBy === 'status' ? 'bd-lane-plain' : 'bd-lane'} aria-label={lane.title || undefined}>
                  {groupBy !== 'status' && (
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
                            {STATUS_LABELS[status]} <span className="bd-count">{list.length}</span>
                          </h3>
                          <div className="bd-column-body">
                            {shown.map(task => (
                              <TaskCard
                                key={task.id}
                                task={task}
                                facts={facts.get(task.id)!}
                                agents={agents}
                                now={now}
                                selected={task.id === selected}
                                onOpen={id => setSelected(cur => (cur === id ? null : id))}
                              />
                            ))}
                            {!list.length && <p className="bd-column-empty">Nothing {STATUS_LABELS[status].toLowerCase()}</p>}
                            {status === 'done' && list.length > DONE_PREVIEW && (
                              <button type="button" className="bd-btn bd-btn-quiet" onClick={() => setShowAllDone(v => !v)}>
                                {showAllDone ? 'Show recent only' : `Show all ${list.length}`}
                              </button>
                            )}
                          </div>
                        </section>
                      );
                    })}
                  </div>
                </section>
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
