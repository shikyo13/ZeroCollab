import { useEffect, useState } from 'react';
import { BackendControls } from './BackendControls';
import { Deployments, DeploymentHistory, type DeploymentFeed } from './Deployments';
import { openServer, type ServerSection } from '../workspace/useWorkspaceRoute';
import './server.css';

const sections: ServerSection[] = ['status', 'updates', 'history'];
const readSection = (): ServerSection => {
  const section = new URL(location.href).searchParams.get('server');
  return sections.includes(section as ServerSection) ? section as ServerSection : 'status';
};

export function ServerPage({ deployments }: { deployments: DeploymentFeed }) {
  const [section, setSection] = useState(readSection);
  useEffect(() => {
    const changed = () => setSection(readSection());
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  return <div className="server-page">
    <div className="workspace-page-heading"><div><h1 id="server-title">Server</h1>
      <p>Manage the local room, install reviewed updates and see previous results.</p></div></div>
    <nav className="server-sections" aria-label="Server sections">
      {sections.map(item => <button key={item} className="bd-btn" aria-current={section === item ? 'page' : undefined}
        onClick={() => openServer(item)}>{item.charAt(0).toUpperCase() + item.slice(1)}</button>)}
    </nav>
    <section hidden={section !== 'status'} aria-label="Server status">
      <div className="server-version bd-panel"><h2>Installed release</h2>
        <p>{deployments.data?.current.version || deployments.data?.current.id || (deployments.error ? 'Status unavailable' : 'Loading…')}</p>
        <button className="bd-btn" onClick={() => openServer('updates')}>View updates</button></div>
      <BackendControls inline />
    </section>
    <section hidden={section !== 'updates'} aria-label="Server updates"><Deployments feed={deployments} /></section>
    <section hidden={section !== 'history'} aria-label="Server history"><DeploymentHistory feed={deployments} /></section>
  </div>;
}
