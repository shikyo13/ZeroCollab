/* Sidebar project picker: always shows which project is attached and jumps to any other. */
import { useEffect, useRef, useState } from 'react';
import { projectLabel, type ProjectSummary } from './projectData';
import { EVENTS } from '../lib/constants';

export function ProjectSwitcher({ projects, active, onOpen }: {
  projects: ProjectSummary[]; active: ProjectSummary | undefined; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  const pick = (id: string) => { setOpen(false); onOpen(id); };
  const busy = (p: ProjectSummary) => p.tasks.working + p.tasks.blocked + p.tasks.review;
  return <div className="pj-switcher" ref={root}>
    <button className="pj-switcher-button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)} title={active ? projectLabel(active) : 'Choose a project'}>
      <span className="pj-switcher-label">Project</span>
      <strong>{active?.name ?? 'Loading…'}</strong>
      <span className="pj-switcher-caret" aria-hidden="true">▾</span>
    </button>
    {open && <div className="pj-switcher-menu" role="listbox" aria-label="Projects">
      {projects.map(p => <button key={p.id} role="option" aria-selected={p.active} className="pj-switcher-item" onClick={() => pick(p.id)}>
        <span className="pj-switcher-name">{p.active ? '● ' : ''}{p.name}</span>
        <small>{p.conversation ? 'No repository' : p.available ? `${busy(p)} active · ${p.tasks.done} done` : 'Repository missing'}</small>
      </button>)}
      <button className="pj-switcher-all" onClick={() => { pick(''); window.dispatchEvent(new Event(EVENTS.newProject)); }}>+ New project</button>
      <button className="pj-switcher-all" onClick={() => pick('')}>All projects</button>
    </div>}
  </div>;
}
