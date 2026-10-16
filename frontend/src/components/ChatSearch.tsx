/* Ctrl+K quick switcher: jump to any chat (open, closed or pre-project history), project or view by title,
 * and to messages of this project's chats by their text or attachment names (task 7a7169ed). Opening a chat
 * goes through channels.js; a message hit then scrolls to the message with reply.js's scrollToMessage. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../lib/api';
import { sessionTitle, useStoreValue } from '../lib/room';
import type { ProjectSummary } from '../workspace/projectData';
import type { View } from '../workspace/useWorkspaceRoute';

type Session = { id: string; title: string; readonly?: boolean };
type Archive = { closed: Session[]; history: Session[] };
type Result = { key: string; label: string; hint: string; run: () => void };
type MessageHit = { id: number; channel: string; closed: boolean; sender: string; timestamp?: number; snippet: string; attachments: string[] };

/** Open the hit's chat, reopening it first if it is closed, then scroll to the message. A failed reopen has
 * already shown its toast and leaves another chat open, so nothing scrolls there. */
async function openHit(hit: MessageHit) {
  if (hit.closed) await window.reopenChatSession?.(hit.channel);
  else window.switchChannel?.(hit.channel);
  if (window.activeChannel === hit.channel) window.scrollToMessage?.(hit.id);
}

const NO_SESSIONS: Session[] = [];
const NO_ARCHIVE: Archive = { closed: [], history: [] };
const VIEWS: [View, string][] = [['board', 'Board'], ['team', 'Team'], ['activity', 'Activity'], ['server', 'Server'], ['projects', 'All projects']];
const RESULTS_SHOWN = 12;
const MESSAGE_RESULTS = 20;
const SEARCH_DELAY_MS = 200;  // typing pause before the message search runs

export function ChatSearch({ open, setOpen, projects, navigate, onOpenProject }: {
  open: boolean; setOpen: (open: boolean) => void; projects: ProjectSummary[];
  navigate: (view: View) => void; onOpenProject: (id: string) => void;
}) {
  const sessions = useStoreValue<Session[]>('chatSessions', NO_SESSIONS);
  const archive = useStoreValue<Archive>('chatArchive', NO_ARCHIVE);
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const [hits, setHits] = useState<MessageHit[]>([]);
  const [searchError, setSearchError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const q = query.trim();
    setHits([]); setSearchError('');
    if (!open || q.length < 2) return;
    let current = true;
    const timer = window.setTimeout(() => {
      api<MessageHit[]>(`/api/messages/search?q=${encodeURIComponent(q)}&limit=${MESSAGE_RESULTS}`)
        .then(found => { if (current) setHits(found); })
        .catch(e => { if (current) setSearchError(`Message search failed: ${e instanceof Error ? e.message : String(e)}`); });
    }, SEARCH_DELAY_MS);
    return () => { current = false; window.clearTimeout(timer); };
  }, [open, query]);

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
    const matches = q ? all.filter(r => r.label.toLowerCase().includes(q)) : all;
    // Keep an exact named view reachable even when matching titles fill the limit.
    const exactView = q ? matches.find(r => r.key.startsWith('v') && r.label.toLowerCase() === q) : undefined;
    const titles = (exactView ? [exactView, ...matches.filter(r => r !== exactView)] : matches).slice(0, RESULTS_SHOWN);
    const messages = hits.map(h => ({
      key: 'm' + h.id,
      label: h.snippet || h.attachments.join(', '),
      hint: [h.sender, sessionTitle(h.channel) + (h.closed ? ' (closed)' : ''),
             h.timestamp ? new Date(h.timestamp * 1000).toLocaleString() : ''].filter(Boolean).join(' · '),
      run: chat(h.channel, () => { void openHit(h); }),
    }));
    return [...titles, ...messages];
  }, [sessions, archive, projects, query, hits, navigate, onOpenProject]);

  useEffect(() => {
    const key = results[index]?.key;
    if (key) document.getElementById(`ws-search-${key}`)?.scrollIntoView({ block: 'nearest' });
  }, [index, results]);

  const choose = (result: Result | undefined) => { if (!result) return; setOpen(false); result.run(); };
  return <dialog ref={dialog} className="ws-search" aria-label="Search chats and projects" onClose={() => setOpen(false)}
    onClick={e => { if (e.target === dialog.current) setOpen(false); }}>
    <input autoFocus value={query} placeholder="Search chats, messages, files and views" aria-label="Search" role="combobox"
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
      {searchError && <li className="ws-search-empty" role="alert">{searchError}</li>}
      {!results.length && !searchError && <li className="ws-search-empty">Nothing in this project's chats, messages or files matches "{query}".</li>}
    </ul>
  </dialog>;
}
