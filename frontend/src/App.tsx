import { useEffect, useState } from 'react';
import Board from './board/Board';
import Team from './team/Team';
import { LegacySlot } from './components/LegacySlot';
import { Icon } from './components/Icon';
import { Help } from './components/Help';
import { brand } from './components/brand';
import { legacy } from './legacy';
import { WorkControls } from './components/WorkControls';
import Projects from './workspace/Projects';
import { ProjectSwitcher } from './workspace/ProjectSwitcher';
import { useProjects } from './workspace/projectData';
import { Deployments, DeploymentNotice, useDeploymentState } from './components/Deployments';

type View = 'projects' | 'chat' | 'board' | 'team' | 'activity';
const VIEWS: View[] = ['projects', 'chat', 'board', 'team', 'activity'];
const validView = (value: string | null): View => VIEWS.includes(value as View) ? value as View : 'chat';

export default function App() {
  const [view, setView] = useState<View>(() => validView(new URL(location.href).searchParams.get('view')));
  const [help, setHelp] = useState(false);
  const projects = useProjects();
  const deployments = useDeploymentState();
  const [focus, setFocusState] = useState(() => new URL(location.href).searchParams.get('project') || '');
  function setFocus(id: string) {
    setFocusState(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set('project', id); else url.searchParams.delete('project');
    history.replaceState({}, '', url);
  }
  const openProject = (id: string) => { setFocus(id); navigate('projects'); };
  const [welcome, setWelcome] = useState(() => {
    try { return localStorage.getItem('workspace-welcome-dismissed') !== '1'; } catch { return true; }
  });
  function navigate(next: View) {
    setView(next);
    const url = new URL(location.href);
    url.searchParams.set('view', next);
    history.pushState({}, '', url);
  }
  useEffect(() => {
    const back = () => setView(validView(new URL(location.href).searchParams.get('view')));
    const showHelp = () => setHelp(true);
    const showTeam = () => navigate('team');
    const toolbar = (event: Event) => {
      const target = (event.target as HTMLElement).closest('button');
      if (target?.id === 'settings-toggle') navigate('chat');
    };
    window.addEventListener('popstate', back);
    window.addEventListener('workspace-help', showHelp);
    window.addEventListener('workspace-team', showTeam);
    legacy.header.addEventListener('click', toolbar, true);
    return () => {
      window.removeEventListener('popstate', back);
      window.removeEventListener('workspace-help', showHelp);
      window.removeEventListener('workspace-team', showTeam);
      legacy.header.removeEventListener('click', toolbar, true);
    };
  }, []);
  useEffect(() => {
    document.body.dataset.workspaceView = view;
    window.dispatchEvent(new CustomEvent('workspace-view-change', { detail: { view } }));
    if (view === 'chat') window.dispatchEvent(new Event('resize'));
  }, [view]);
  const dismissWelcome = () => {
    setWelcome(false);
    try { localStorage.setItem('workspace-welcome-dismissed', '1'); } catch { /* storage may be disabled */ }
  };
  return <div className="workspace-shell">
    <a className="workspace-skip" href="#workspace-content">Skip to workspace</a>
    <nav className="workspace-nav" aria-label="Workspace">
      <a className="workspace-brand" href="?view=chat" aria-label={`${brand.name} - Chat`} onClick={e => { e.preventDefault(); navigate('chat'); }}><img src={brand.logo} alt="" /><span>{brand.name}</span></a>
      <ProjectSwitcher projects={projects.projects} active={projects.active} onOpen={openProject} />
      <div className="workspace-nav-items">
        <button aria-current={view === 'projects' ? 'page' : undefined} onClick={() => navigate('projects')}><Icon name="projects" /><span>Projects</span></button>
        <button aria-current={view === 'chat' ? 'page' : undefined} onClick={() => navigate('chat')}><Icon name="chat" /><span>Chat</span></button>
        <button aria-current={view === 'board' ? 'page' : undefined} onClick={() => navigate('board')}><Icon name="board" /><span>Board</span></button>
        <button aria-current={view === 'team' ? 'page' : undefined} onClick={() => navigate('team')}><Icon name="team" /><span>Team</span></button>
        <button aria-current={view === 'activity' ? 'page' : undefined} onClick={() => navigate('activity')}><Icon name="activity" /><span>Activity</span></button>
      </div>
      <div className="workspace-nav-bottom"><button onClick={() => setHelp(true)}><Icon name="help" /><span>Quick guide</span></button></div>
    </nav>
    <div className="workspace-main">
      <LegacySlot node={legacy.header} className="workspace-toolbar" />
      <LegacySlot node={legacy.controls} className="workspace-controlbar" />
      <LegacySlot node={legacy.approvals} className="workspace-approvals" />
      <WorkControls navigate={navigate} />
      <DeploymentNotice feed={deployments} onOpen={() => navigate('activity')} />
      {welcome && <div className="workspace-welcome"><span>Start in Chat. Track work on the Board. Manage agents in Team.</span><button onClick={() => setHelp(true)}>Quick guide</button><button onClick={dismissWelcome} aria-label="Dismiss welcome">✕</button></div>}
      <main id="workspace-content" className="workspace-content" tabIndex={-1}>
        <section className="workspace-projects" hidden={view !== 'projects'} aria-label="Projects"><Projects {...projects} focus={focus} setFocus={setFocus} navigate={navigate} /></section>
        <section className="workspace-chat" hidden={view !== 'chat'} aria-label="Shared chat"><LegacySlot node={legacy.chat} className="workspace-chat-slot" /></section>
        <section className="workspace-board" hidden={view !== 'board'} aria-label="Task board"><Board /></section>
        <section className="workspace-team" hidden={view !== 'team'} aria-label="Team"><Team /></section>
        <section className="workspace-activity" hidden={view !== 'activity'} aria-labelledby="activity-title"><div className="workspace-page-heading"><div><span className="workspace-eyebrow">ROOM OPERATIONS</span><h1 id="activity-title">Activity & controls</h1><p>Agent requests, recent tool calls and the Changes view. Repositories are in Projects; agents and models are in Team.</p></div><button onClick={() => navigate('team')}>Manage team</button></div><Deployments feed={deployments} /><LegacySlot node={legacy.runtime} /></section>
      </main>
    </div>
    <Help open={help} onClose={() => setHelp(false)} />
  </div>;
}
