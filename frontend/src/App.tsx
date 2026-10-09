import { useState } from 'react';
import { BackendControls } from './components/BackendControls';
import Board from './board/Board';
import Team from './team/Team';
import { LegacySlot } from './components/LegacySlot';
import { Icon } from './components/Icon';
import { Help } from './components/Help';
import { brand } from './components/brand';
import { legacy } from './legacy';
import { WorkControls } from './components/WorkControls';
import Projects from './workspace/Projects';
import SidePanel from './sidepanel/SidePanel';
import FileView from './sidepanel/FileView';
import { ProjectSwitcher } from './workspace/ProjectSwitcher';
import { useProjects } from './workspace/projectData';
import { useWorkspaceRoute, type View } from './workspace/useWorkspaceRoute';
import { DeploymentNotice, useDeploymentState } from './components/Deployments';
import { WelcomeBar } from './components/WelcomeBar';
import { ActivityPage } from './components/ActivityPage';

const NAV_ITEMS: { view: View; label: string }[] = [
  { view: 'projects', label: 'Projects' },
  { view: 'chat', label: 'Chat' },
  { view: 'board', label: 'Board' },
  { view: 'team', label: 'Team' },
  { view: 'activity', label: 'Activity' },
];

export default function App() {
  const { view, navigate, focus, setFocus } = useWorkspaceRoute();
  const [help, setHelp] = useState(false);
  const projects = useProjects();
  const deployments = useDeploymentState(view === 'activity');
  const openProject = (id: string) => { setFocus(id); navigate('projects'); };
  const openHelp = () => setHelp(true);
  return <div className="workspace-shell">
    <a className="workspace-skip" href="#workspace-content">Skip to workspace</a>
    <nav className="workspace-nav" aria-label="Workspace">
      <a className="workspace-brand" href="?view=chat" aria-label={`${brand.name} - Chat`} onClick={e => { e.preventDefault(); navigate('chat'); }}><img src={brand.logo} alt="" /><span>{brand.name}</span></a>
      <ProjectSwitcher projects={projects.projects} active={projects.active} onOpen={openProject} />
      <div className="workspace-nav-items">
        {NAV_ITEMS.map(item => (
          <button key={item.view} aria-current={view === item.view ? 'page' : undefined} onClick={() => navigate(item.view)}>
            <Icon name={item.view} /><span>{item.label}</span>
          </button>
        ))}
      </div>
      <div className="workspace-nav-bottom"><BackendControls /><button onClick={openHelp}><Icon name="help" /><span>Quick guide</span></button></div>
    </nav>
    <div className="workspace-main">
      <LegacySlot node={legacy.header} className="workspace-toolbar" />
      <LegacySlot node={legacy.controls} className="workspace-controlbar" />
      <LegacySlot node={legacy.approvals} className="workspace-approvals" />
      <WorkControls navigate={navigate} />
      <DeploymentNotice feed={deployments} onOpen={() => navigate('activity')} />
      <WelcomeBar onHelp={openHelp} />
      <SidePanel fileView={(request, active, close) => <FileView request={request} active={active} onClose={close} />}><main id="workspace-content" className="workspace-content" tabIndex={-1}>
        <section className="workspace-projects" hidden={view !== 'projects'} aria-label="Projects"><Projects {...projects} focus={focus} setFocus={setFocus} navigate={navigate} /></section>
        <section className="workspace-chat" hidden={view !== 'chat'} aria-label="Shared chat"><LegacySlot node={legacy.chat} className="workspace-chat-slot" /></section>
        <section className="workspace-board" hidden={view !== 'board'} aria-label="Task board"><Board /></section>
        <section className="workspace-team" hidden={view !== 'team'} aria-label="Team"><Team /></section>
        <section className="workspace-activity" hidden={view !== 'activity'} aria-labelledby="activity-title"><ActivityPage deployments={deployments} onManageTeam={() => navigate('team')} /></section>
      </main></SidePanel>
    </div>
    <Help open={help} onClose={() => setHelp(false)} />
  </div>;
}

