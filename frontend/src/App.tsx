import { useState } from 'react';
import { SessionNotice } from './components/SessionNotice';
import Board from './board/Board';
import Team from './team/Team';
import { LegacySlot } from './components/LegacySlot';
import { Help } from './components/Help';
import { SettingsDialog } from './components/SettingsDialog';
import { legacy } from './legacy';
import { WorkControls } from './components/WorkControls';
import { HeaderBar } from './components/HeaderBar';
import { AttentionBell } from './components/AttentionBell';
import { ChatSearch } from './components/ChatSearch';
import Projects from './workspace/Projects';
import SidePanel from './sidepanel/SidePanel';
import FileView from './sidepanel/FileView';
import { Sidebar } from './workspace/Sidebar';
import { useProjects } from './workspace/projectData';
import { useWorkspaceRoute } from './workspace/useWorkspaceRoute';
import { DeploymentNotice, useDeploymentState } from './components/Deployments';
import { ServerPage } from './components/ServerPage';
import { openServer } from './workspace/useWorkspaceRoute';
import { FollowToggle } from './follow/Follow';
import LiveTeam from './live/LiveTeam';
import './shell.css';

export default function App() {
  const { view, navigate, focus, setFocus, historySession, browseHistory } = useWorkspaceRoute();
  const [help, setHelp] = useState(false);
  const [search, setSearch] = useState(false);
  const projects = useProjects();
  const deployments = useDeploymentState(view === 'activity' || view === 'server');
  const openProject = (id: string) => { setFocus(id); navigate('projects'); };
  return <div className="workspace-shell">
    <a className="workspace-skip" href="#workspace-content">Skip to workspace</a>
    <Sidebar view={view} navigate={navigate} projects={projects.projects} active={projects.active} deployments={deployments}
      viewedProject={focus} historySession={historySession} onBrowseHistory={browseHistory} onRefreshProjects={projects.refresh}
      onOpenProject={openProject} onSearch={() => setSearch(true)} onHelp={() => setHelp(true)} />
    <div className="workspace-main">
      {/* One header row. Stop/Resume/Changes and the room mode line live on the Activity page
          (inside legacy.runtime); the goal is a header chip while one is open. */}
      <LegacySlot node={legacy.header} className="workspace-toolbar" />
      <HeaderBar view={view} projectName={projects.active?.name ?? ''} onTeam={() => navigate('team')}
        bell={<AttentionBell projects={projects.projects} deployments={deployments} navigate={navigate} openProject={openProject} />} follow={<FollowToggle onStart={() => navigate('board')} />} />
      <LegacySlot node={legacy.approvals} className="workspace-approvals" />
      <WorkControls navigate={navigate} />
      <SessionNotice />
      <DeploymentNotice feed={deployments} onOpen={() => openServer('updates')} />
      <SidePanel fileView={(request, active, close) => <FileView request={request} active={active} onClose={close} />}><main id="workspace-content" className="workspace-content" tabIndex={-1}>
        <section className="workspace-projects" hidden={view !== 'projects'} aria-label="Projects"><Projects {...projects} focus={focus} setFocus={setFocus} navigate={navigate}
          historySession={historySession} browseHistory={browseHistory} /></section>
        <section className="workspace-chat" hidden={view !== 'chat'} aria-label="Shared chat"><LegacySlot node={legacy.chat} className="workspace-chat-slot" /></section>
        <section className="workspace-board" hidden={view !== 'board'} aria-label="Task board"><Board /></section>
        <section className="workspace-live" hidden={view !== 'live'} aria-label="Live view"><LiveTeam active={view === 'live'} navigate={navigate} /></section>
        <section className="workspace-team" hidden={view !== 'team'} aria-label="Team"><Team /></section>
        <section className="workspace-server" hidden={view !== 'server'} aria-labelledby="server-title"><ServerPage active={view === 'server'} deployments={deployments} /></section>
      </main></SidePanel>
    </div>
    <ChatSearch open={search} setOpen={setSearch} projects={projects.projects} navigate={navigate} onOpenProject={openProject} />
    <Help open={help} onClose={() => setHelp(false)} />
    <SettingsDialog />
  </div>;
}
