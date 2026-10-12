/* Workspace sidebar, organized like the Codex and Claude desktop apps: new chat and search
 * first, the views, then projects with the active project's chats nested under it.
 * The chat rows are channels.js's own list (rename, close, unread, closed & older), moved
 * here rather than re-implemented, so session behaviour stays in one place. */
import { useEffect, useRef, useState } from 'react';
import type { DeploymentFeed } from '../components/Deployments';
import { openServer } from './useWorkspaceRoute';
import { Icon } from '../components/Icon';
import { LegacySlot } from '../components/LegacySlot';
import { brand } from '../components/brand';
import { legacy } from '../legacy';
import { EVENTS } from '../lib/constants';
import { useRuntimeState } from '../lib/room';
import type { ProjectSummary } from './projectData';
import type { View } from './useWorkspaceRoute';
import { ProjectActions } from './ProjectActions';
import './projectNavigation.css';
import { runtime } from '../lib/api';
import { useAction } from '../board/shared';

export function Sidebar({ view, navigate, projects, active, deployments, onOpenProject, onBrowseHistory, viewedProject, historySession, onRefreshProjects, onSearch, onHelp }: {
  view: View;
  deployments: DeploymentFeed;
  navigate: (view: View) => void;
  projects: ProjectSummary[];
  active: ProjectSummary | undefined;
  onOpenProject: (id: string) => void;
  onBrowseHistory: (project: string, session: string) => void;
  viewedProject: string;
  historySession: string;
  onRefreshProjects: () => Promise<void>;
  onSearch: () => void;
  onHelp: () => void;
}) {
  const { data } = useRuntimeState();
  const [chatsOpen, setChatsOpen] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [undo, setUndo] = useState<{ id: string; name: string } | null>(null);
  const [notice, setNotice] = useState('');
  const undoAction = useAction();
  const drawer = useRef<HTMLDivElement>(null);
  const drawerButton = useRef<HTMLButtonElement>(null);
  const closeDrawer = () => { setDrawerOpen(false); drawerButton.current?.focus(); };
  useEffect(() => {
    if (!drawerOpen) return;
    drawer.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { setDrawerOpen(false); drawerButton.current?.focus(); } };
    const outside = (e: PointerEvent) => { if (!drawer.current?.contains(e.target as Node) && !drawerButton.current?.contains(e.target as Node)) setDrawerOpen(false); };
    document.addEventListener('keydown', key); document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', key); document.removeEventListener('pointerdown', outside); };
  }, [drawerOpen]);
  const updateAvailable = !deployments.error && !!deployments.data?.releases.some(r => r.relation === 'newer');
  const serverLabel = updateAvailable ? 'Server: Update available' : 'Server';
  const agents = Object.values(data?.agents ?? {}).filter(a => !a.archived).length;
  const busy = active ? active.tasks.working + active.tasks.review + active.tasks.blocked : 0;
  // The active project first, then the most recently updated ones.
  const others = projects.filter(p => !p.active && !p.archived).sort((a, b) => b.updated - a.updated);
  const archived = projects.filter(p => p.archived && !p.active);
  const newChat = () => { setChatsOpen(true); navigate('chat'); window.showChannelCreateDialog?.(); };
  const newProject = () => { onOpenProject(''); window.dispatchEvent(new Event(EVENTS.newProject)); };
  const item = (target: View, label: string, extra?: React.ReactNode) =>
    <button className="ws-nav-item" aria-current={view === target ? 'page' : undefined} title={label} onClick={() => navigate(target)}>
      <Icon name={target === 'server' ? 'settings' : target} size={18} /><span>{label}</span>{extra}
    </button>;
  const changed = async (p: ProjectSummary, archived?: boolean) => {
    if (archived === true) { setUndo({ id: p.id, name: p.name }); setArchivedOpen(true); setNotice(`${p.name} archived. Saved data retained.`); }
    else if (archived === false) { if (undo?.id === p.id) setUndo(null); setNotice(`${p.name} restored.`); }
    else setNotice('Project name saved.');
    await onRefreshProjects();
    if (archived !== undefined) requestAnimationFrame(() => drawer.current?.querySelector<HTMLButtonElement>(`[data-project-options="${CSS.escape(p.id)}"]`)?.focus());
  };
  const projectRow = (p: ProjectSummary) => {
    const open = p.active ? chatsOpen : expanded.has(p.id);
    const toggle = () => p.active ? setChatsOpen(v => !v) : setExpanded(prev => { const next = new Set(prev); if (next.has(p.id)) next.delete(p.id); else next.add(p.id); return next; });
    return <div className={`ws-project${p.active ? ' is-active' : ''}`} key={p.id}>
      <div className="pn-project-row"><button className="pn-project-disclosure" aria-label={`${open ? 'Collapse' : 'Expand'} ${p.name}`} aria-expanded={open} aria-controls={`pn-chats-${p.id}`} onClick={toggle}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} /></button>
        <button className="ws-nav-item ws-project-row" title={p.path || p.name} onClick={() => { onOpenProject(p.id); if (drawerOpen) closeDrawer(); }}><span>{p.name}</span>{p.active && <i className="ws-live" aria-label="Running project" />}</button>
        <ProjectActions project={p} onChanged={value => void changed(p, value)} onDetails={() => { onOpenProject(p.id); if (drawerOpen) closeDrawer(); }} /></div>
      <div id={`pn-chats-${p.id}`} hidden={!open}>
        {p.active ? <div onClickCapture={e => { if ((e.target as HTMLElement).closest('.channel-sidebar-row-label, .channel-more-item')) { navigate('chat'); if (drawerOpen) closeDrawer(); } }}>
          <LegacySlot node={legacy.chatList} className="ws-chats" />
        </div> : <div className="pn-saved-chats">
          {p.sessions?.map(s => <button key={s.id} aria-current={view === 'projects' && viewedProject === p.id && historySession === s.id ? 'page' : undefined}
            title={`Browse ${s.title} · ${p.name}`} onClick={() => { onBrowseHistory(p.id, s.id); if (drawerOpen) closeDrawer(); }}>{s.title}</button>)}
          {!p.sessions?.length && <small>No saved chats</small>}
          <button className="pn-project-detail" onClick={() => { onOpenProject(p.id); if (drawerOpen) closeDrawer(); }}>Project details</button>
        </div>}
      </div>
    </div>;
  };
  return <nav className="workspace-nav" aria-label="Workspace">
    <a className="workspace-brand" href="?view=chat" aria-label={`${brand.name} - Chat`} onClick={e => { e.preventDefault(); navigate('chat'); }}>
      <img src={brand.logo} alt="" /><span>{brand.name}</span>
    </a>
    <div className="ws-nav-group">
      <button className="ws-nav-item pn-project-access" ref={drawerButton} aria-expanded={drawerOpen} aria-controls="pn-project-drawer" onClick={() => setDrawerOpen(v => !v)}><Icon name="projects" size={18} /><span>Browse projects</span></button>
      <button className="ws-nav-item" onClick={newChat} disabled={!active} title="New chat"><Icon name="compose" size={18} /><span>New chat</span></button>
      <button className="ws-nav-item" onClick={onSearch} aria-keyshortcuts="Control+K" title="Search (Ctrl+K)"><Icon name="search" size={18} /><span>Search</span><kbd>Ctrl K</kbd></button>
    </div>
    <div className="ws-nav-group">
      {item('board', 'Board', busy ? <small className="ws-count">{busy} active</small> : null)}
      {item('live', 'Live')}
      {item('team', 'Team', agents ? <small className="ws-count">{agents}</small> : null)}
    </div>
    <div className={`ws-projects${drawerOpen ? ' pn-drawer-open' : ''}`} id="pn-project-drawer" ref={drawer}>
      <button className="pn-drawer-close" onClick={closeDrawer}>Close projects</button>
      <div className="ws-section"><span>Projects</span><button aria-label="New project" title="New project" onClick={newProject}><Icon name="plus" size={16} /></button></div>
      {active && projectRow(active)}
      {others.map(projectRow)}
      {undo && <div className="pn-undo"><span>{undo.name} archived. Data retained.</span><button disabled={undoAction.busy} onClick={async () => {
        if (await undoAction.run(() => runtime(`/projects/${encodeURIComponent(undo.id)}/archive`, { archived: false }))) { const p = projects.find(p => p.id === undo.id); setUndo(null); if (p) await changed(p, false); else await onRefreshProjects(); }
      }}>Undo archive</button>{undoAction.error && <p role="alert">{undoAction.error}</p>}</div>}
      {archived.length > 0 && <><button className="ws-nav-item ws-all" aria-expanded={archivedOpen} aria-controls="pn-archived" onClick={() => setArchivedOpen(v => !v)}>Archived projects ({archived.length})</button>
        <div id="pn-archived" hidden={!archivedOpen}>{archived.map(projectRow)}</div></>}
      <button className="ws-nav-item ws-all" aria-current={view === 'projects' && !historySession ? 'page' : undefined} onClick={() => { onOpenProject(''); if (drawerOpen) closeDrawer(); }}>All projects</button>
    </div>
    <div className="pn-announcement" role="status">{notice}</div>
    <div className="workspace-nav-bottom">
      <button className="ws-nav-item backend-trigger" aria-current={view === 'server' ? 'page' : undefined} onClick={() => openServer('status')} title={serverLabel} aria-label={serverLabel}>
        <i className="backend-indicator" aria-hidden="true"><i className={`backend-dot ${deployments.ready ? 'is-running' : ''}`} /></i>
        <span>Server</span>{updateAvailable && <small className="backend-status">Update available</small>}
      </button>
      <button className="ws-nav-item" onClick={() => window.toggleSettings?.()} title="Settings"><Icon name="settings" size={18} /><span>Settings</span></button>
      <button className="ws-nav-item" onClick={onHelp} title="Guide"><Icon name="help" size={18} /><span>Guide</span></button>
    </div>
  </nav>;
}
