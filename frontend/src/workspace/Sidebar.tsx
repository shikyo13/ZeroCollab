/* Workspace sidebar, organized like the Codex and Claude desktop apps: new chat and search
 * first, the views, then projects with the active project's chats nested under it.
 * The chat rows are channels.js's own list (rename, close, unread, closed & older), moved
 * here rather than re-implemented, so session behaviour stays in one place. */
import { useState } from 'react';
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

const PROJECTS_SHOWN = 6;

export function Sidebar({ view, navigate, projects, active, deployments, onOpenProject, onSearch, onHelp }: {
  view: View;
  deployments: DeploymentFeed;
  navigate: (view: View) => void;
  projects: ProjectSummary[];
  active: ProjectSummary | undefined;
  onOpenProject: (id: string) => void;
  onSearch: () => void;
  onHelp: () => void;
}) {
  const { data } = useRuntimeState();
  const [chatsOpen, setChatsOpen] = useState(true);
  const serverLabel = !deployments.data && !deployments.error ? 'Checking updates'
    : deployments.error || !deployments.fresh ? 'Updates blocked'
    : deployments.ready ? deployments.data?.pending.length || deployments.data?.maintenance.admission !== 'open' || deployments.data?.maintenance.writes === 'blocked' ? 'Updates busy' : 'Updates ready' : 'Updating';
  const agents = Object.values(data?.agents ?? {}).filter(a => !a.archived).length;
  const requests = data?.approvals?.length ?? 0;
  const busy = active ? active.tasks.working + active.tasks.review + active.tasks.blocked : 0;
  // The active project first, then the most recently updated ones.
  const others = projects.filter(p => !p.active).sort((a, b) => b.updated - a.updated).slice(0, PROJECTS_SHOWN - 1);
  const newChat = () => { setChatsOpen(true); navigate('chat'); window.showChannelCreateDialog?.(); };
  const newProject = () => { onOpenProject(''); window.dispatchEvent(new Event(EVENTS.newProject)); };
  const item = (target: View, label: string, extra?: React.ReactNode) =>
    <button className="ws-nav-item" aria-current={view === target ? 'page' : undefined} title={label} onClick={() => navigate(target)}>
      <Icon name={target === 'server' ? 'settings' : target} size={18} /><span>{label}</span>{extra}
    </button>;
  return <nav className="workspace-nav" aria-label="Workspace">
    <a className="workspace-brand" href="?view=chat" aria-label={`${brand.name} - Chat`} onClick={e => { e.preventDefault(); navigate('chat'); }}>
      <img src={brand.logo} alt="" /><span>{brand.name}</span>
    </a>
    <div className="ws-nav-group">
      <button className="ws-nav-item" onClick={newChat} disabled={!active} title="New chat"><Icon name="compose" size={18} /><span>New chat</span></button>
      <button className="ws-nav-item" onClick={onSearch} aria-keyshortcuts="Control+K" title="Search (Ctrl+K)"><Icon name="search" size={18} /><span>Search</span><kbd>Ctrl K</kbd></button>
    </div>
    <div className="ws-nav-group">
      {item('board', 'Board', busy ? <small className="ws-count">{busy} active</small> : null)}
      {item('team', 'Team', agents ? <small className="ws-count">{agents}</small> : null)}
      {item('activity', 'Activity', requests ? <small className="ws-badge">{requests} {requests === 1 ? 'request' : 'requests'}</small> : null)}
    </div>
    <div className="ws-projects">
      <div className="ws-section"><span>Projects</span><button aria-label="New project" title="New project" onClick={newProject}><Icon name="plus" size={16} /></button></div>
      {active && <div className="ws-project is-active">
        <button className="ws-nav-item ws-project-row" aria-expanded={chatsOpen} onClick={() => setChatsOpen(v => !v)} title={active.path || active.name}>
          <Icon name={chatsOpen ? 'chevron-down' : 'chevron-right'} size={14} /><span>{active.name}</span><i className="ws-live" aria-label="Active project" />
        </button>
        {/* Kept mounted while collapsed: channels.js renders into this node by id. */}
        {/* Capture before channels.js rebuilds the clicked row and detaches the event target. */}
        <div hidden={!chatsOpen} onClickCapture={e => { if ((e.target as HTMLElement).closest('.channel-sidebar-row-label, .channel-more-item')) navigate('chat'); }}>
          <LegacySlot node={legacy.chatList} className="ws-chats" />
        </div>
      </div>}
      {others.map(p => <button key={p.id} className="ws-nav-item ws-project-row" onClick={() => onOpenProject(p.id)} title={`Open ${p.name}`}>
        <Icon name="chevron-right" size={14} /><span>{p.name}</span>
      </button>)}
      <button className="ws-nav-item ws-all" aria-current={view === 'projects' ? 'page' : undefined} onClick={() => onOpenProject('')}>All projects</button>
    </div>
    <div className="workspace-nav-bottom">
      <button className="ws-nav-item backend-trigger" aria-current={view === 'server' ? 'page' : undefined} onClick={() => openServer('status')} title={`Server: ${serverLabel}`} aria-label={`Server: ${serverLabel}`}>
        <i className="backend-indicator" aria-hidden="true"><i className={`backend-dot ${deployments.ready ? 'is-running' : ''}`} /></i>
        <span>Server</span><small className="backend-status">{serverLabel}</small>
      </button>
      <button className="ws-nav-item" onClick={() => window.toggleSettings?.()} title="Settings"><Icon name="settings" size={18} /><span>Settings</span></button>
      <button className="ws-nav-item" onClick={onHelp} title="Guide"><Icon name="help" size={18} /><span>Guide</span></button>
    </div>
  </nav>;
}
