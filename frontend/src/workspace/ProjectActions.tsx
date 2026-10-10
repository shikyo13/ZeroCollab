import { useEffect, useId, useRef, useState } from 'react';
import { runtime } from '../lib/api';
import { useAction } from '../board/shared';
import { Icon } from '../components/Icon';
import type { ProjectSummary } from './projectData';

export function ProjectActions({ project, onChanged, onDetails }: {
  project: ProjectSummary; onChanged: (archived?: boolean) => void; onDetails: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'rename' | 'archive' | null>(null);
  const [name, setName] = useState(project.name);
  const { run, busy, error, setError } = useAction();
  const instance = useId();
  const errorId = `pn-name-error-${instance}`;
  const reasonId = `pn-archive-reason-${instance}`;
  const close = () => { dialog.current?.close(); setMode(null); setOpen(false); setError(''); opener.current?.focus(); };
  useEffect(() => {
    if (mode) { dialog.current?.showModal(); if (mode === 'rename') { input.current?.focus(); input.current?.select(); } }
    else dialog.current?.close();
  }, [mode]);
  useEffect(() => {
    if (!open || mode) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, mode]);
  const archive = async (archived: boolean) => {
    if (await run(() => runtime(`/projects/${encodeURIComponent(project.id)}/archive`, { archived }))) { close(); onChanged(archived); }
  };
  return <div className={`pn-project-actions${open ? ' is-open' : ''}`} ref={root}
    onKeyDown={e => { if (e.key === 'Escape' && !mode) { e.preventDefault(); e.stopPropagation(); close(); } }}>
    <button className="pn-options-toggle" data-project-options={project.id} ref={opener} aria-label={`Project options for ${project.name}`} aria-expanded={open}
      onClick={() => setOpen(v => !v)}><Icon name="more" size={17} /></button>
    {open && <div className="pn-options">
      <button onClick={() => { close(); onDetails(); }}>Project details</button>
      <button onClick={() => { setName(project.name); setError(''); setMode('rename'); }}>Rename project</button>
      {project.archived ? <button disabled={busy} onClick={() => void archive(false)}>Restore project</button>
        : <><button disabled={project.active} aria-describedby={project.active ? reasonId : undefined} onClick={() => { setError(''); setMode('archive'); }}>Archive project</button>
          {project.active && <small id={reasonId}>Switch to another project before archiving the running room.</small>}</>}
      {!mode && error && <p role="alert">{error}</p>}
    </div>}
    <dialog className="pn-project-dialog" ref={dialog} aria-labelledby={`pn-dialog-title-${instance}`} onCancel={e => { e.preventDefault(); if (!busy) close(); }} onClose={() => { setMode(null); setOpen(false); setError(''); opener.current?.focus(); }}>
      <h2 id={`pn-dialog-title-${instance}`}>{mode === 'rename' ? 'Rename project' : 'Archive project?'}</h2>
      {mode === 'rename' ? <form onSubmit={async e => {
        e.preventDefault();
        if (!name.trim()) { setError('Enter a project name.'); input.current?.focus(); return; }
        if (await run(() => runtime(`/projects/${encodeURIComponent(project.id)}`, { name: name.trim() }))) { close(); onChanged(); }
      }}><p>Change the display name of {project.name}. Its chats, tasks and files stay with the same project.</p>
        <label>Project name<input ref={input} value={name} onChange={e => setName(e.target.value)} maxLength={60} aria-invalid={error ? true : undefined} aria-describedby={errorId} /></label>
        <p id={errorId} role={error ? 'alert' : undefined}>{error}</p>
        <div className="pn-dialog-actions"><button type="button" disabled={busy} onClick={close}>Cancel</button><button disabled={busy}>Save name</button></div></form>
        : <><p>Hide {project.name} from the main project list? Chats, tasks, reviews and files are kept. Find it under Archived projects to restore it.</p>
          {error && <p role="alert">{error}</p>}<div className="pn-dialog-actions"><button disabled={busy} onClick={close}>Keep project</button><button disabled={busy} onClick={() => void archive(true)}>Archive project</button></div></>}
    </dialog>
  </div>;
}
