/* Ctrl+K quick switcher: jump to any chat (open, closed or pre-project history), project or view
 * by title. It searches titles, not message text. Opening a chat goes through channels.js. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useStoreValue } from '../lib/room';
import type { ProjectSummary } from '../workspace/projectData';
import type { View } from '../workspace/useWorkspaceRoute';

type Session = { id: string; title: string; readonly?: boolean };
type Archive = { closed: Session[]; history: Session[] };
type Result = { key: string; label: string; hint: string; run: () => void };

const NO_SESSIONS: Session[] = [];
const NO_ARCHIVE: Archive = { closed: [], history: [] };
const VIEWS: [View, string][] = [['board', 'Board'], ['team', 'Team'], ['activity', 'Activity'], ['projects', 'All projects']];
const RESULTS_SHOWN = 12;

export function ChatSearch({ open, setOpen, projects, navigate, onOpenProject }: {
  open: boolean; setOpen: (open: boolean) => void; projects: ProjectSummary[];
  navigate: (view: View) => void; onOpenProject: (id: string) => void;
}) {
  const sessions = useStoreValue<Session[]>('chatSessions', NO_SESSIONS);
  const archive = useStoreValue<Archive>('chatArchive', NO_ARCHIVE);
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen(true); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [setOpen]);
  useEffect(() => {
    if (open) { setQuery(''); setIndex(0); dialog.current?.showModal(); } else dialog.current?.close();
  }, [open]);

  const results = useMemo(() => {
    const chat = (id: string, go: () => void) => () => { navigate('chat'); go(); };
    const all: Result[] = [
      ...sessions.filter(s => !s.readonly).map(s => ({ key: 'o' + s.id, label: s.title, hint: 'Chat', run: chat(s.id, () => window.switchChannel?.(s.id)) })),
      ...archive.closed.map(s => ({ key: 'c' + s.id, label: s.title, hint: 'Closed chat · reopen', run: chat(s.id, () => window.reopenChatSession?.(s.id)) })),
      ...archive.history.map(s => ({ key: 'h' + s.id, label: s.title, hint: 'Before projects · read-only', run: chat(s.id, () => window.viewChatHistory?.(s.id)) })),
      ...projects.map(p => ({ key: 'p' + p.id, label: p.name, hint: p.active ? 'Project · active' : 'Project', run: () => onOpenProject(p.id) })),
      ...VIEWS.map(([view, label]) => ({ key: 'v' + view, label, hint: 'View', run: () => navigate(view) })),
    ];
    const q = query.trim().toLowerCase();
    return (q ? all.filter(r => r.label.toLowerCase().includes(q)) : all).slice(0, RESULTS_SHOWN);
  }, [sessions, archive, projects, query, navigate, onOpenProject]);

  const choose = (result: Result | undefined) => { if (!result) return; setOpen(false); result.run(); };
  return <dialog ref={dialog} className="ws-search" aria-label="Search chats and projects" onClose={() => setOpen(false)}
    onClick={e => { if (e.target === dialog.current) setOpen(false); }}>
    <input autoFocus value={query} placeholder="Search chats, projects and views" aria-label="Search" role="combobox"
      aria-expanded="true" aria-controls="ws-search-results" aria-activedescendant={results[index] ? `ws-search-${results[index].key}` : undefined}
      onChange={e => { setQuery(e.target.value); setIndex(0); }}
      onKeyDown={e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); setIndex(i => Math.min(i + 1, results.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setIndex(i => Math.max(i - 1, 0)); }
        else if (e.key === 'Enter') { e.preventDefault(); choose(results[index]); }
      }} />
    <ul id="ws-search-results" role="listbox">
      {results.map((r, i) => <li key={r.key} id={`ws-search-${r.key}`} role="option" aria-selected={i === index}
        onMouseEnter={() => setIndex(i)} onClick={() => choose(r)}><span>{r.label}</span><small>{r.hint}</small></li>)}
      {!results.length && <li className="ws-search-empty">No chat, project or view matches "{query}". Search covers titles, not message text.</li>}
    </ul>
  </dialog>;
}
