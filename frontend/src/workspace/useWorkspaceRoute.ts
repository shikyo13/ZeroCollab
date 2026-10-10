/* The workspace view and focused project, kept in the URL (?view=, ?project=)
 * so reloads, links and the browser Back button land on the same page. */
import { useCallback, useEffect, useState } from 'react';
import { EVENTS } from '../lib/constants';
import { legacy } from '../legacy';

export const VIEWS = ['projects', 'chat', 'board', 'live', 'team', 'activity', 'server'] as const;
export type View = typeof VIEWS[number];

export type ServerSection = 'status' | 'updates' | 'history';
export function openServer(section: ServerSection = 'status') {
  const url = new URL(location.href);
  url.searchParams.set('view', 'server');
  url.searchParams.set('server', section);
  history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

const DEFAULT_VIEW: View = 'chat';
const validView = (value: string | null): View => (VIEWS.includes(value as View) ? (value as View) : DEFAULT_VIEW);
const param = (name: string) => new URL(location.href).searchParams.get(name);

export function useWorkspaceRoute() {
  const [view, setView] = useState<View>(() => validView(param('view')));
  const [focus, setFocusState] = useState(() => param('project') || '');
  const [historySession, setHistorySession] = useState(() => param('history') || '');

  const navigate = useCallback((next: View) => {
    setView(next);
    const url = new URL(location.href);
    url.searchParams.set('view', next);
    url.searchParams.delete('history');
    setHistorySession('');
    history.pushState({}, '', url);
  }, []);

  // Focusing a project replaces the history entry: it refines the page rather than leaving it.
  const setFocus = useCallback((id: string) => {
    setFocusState(id);
    setHistorySession('');
    const url = new URL(location.href);
    if (id) url.searchParams.set('project', id);
    else url.searchParams.delete('project');
    url.searchParams.delete('history');
    history.replaceState({}, '', url);
  }, []);

  // Browsing saved history never changes the legacy channel, composer or runtime project.
  const browseHistory = useCallback((project: string, session: string) => {
    setFocusState(project); setHistorySession(session); setView('projects');
    const url = new URL(location.href);
    url.searchParams.set('view', 'projects');
    url.searchParams.set('project', project);
    url.searchParams.set('history', session);
    history.pushState({}, '', url);
  }, []);

  useEffect(() => {
    // Back/Forward: the entry's URL is the source of truth for both the view and the project.
    const back = () => {
      setView(validView(param('view')));
      setFocusState(param('project') || '');
      setHistorySession(param('history') || '');
    };
    const showTeam = () => navigate('team');
    // The legacy Settings button lives in the chat header; open the chat so its panel is visible.
    const toolbar = (event: Event) => {
      const target = (event.target as HTMLElement).closest('button');
      if (target?.id === 'settings-toggle') navigate('chat');
    };
    window.addEventListener('popstate', back);
    window.addEventListener(EVENTS.team, showTeam);
    legacy.header.addEventListener('click', toolbar, true);
    return () => {
      window.removeEventListener('popstate', back);
      window.removeEventListener(EVENTS.team, showTeam);
      legacy.header.removeEventListener('click', toolbar, true);
    };
  }, [navigate]);

  useEffect(() => {
    document.body.dataset.workspaceView = view;
    // Legacy chat layout measures itself on resize; it was hidden while another view showed.
    if (view === 'chat') window.dispatchEvent(new Event('resize'));
  }, [view]);

  return { view, navigate, focus, setFocus, historySession, browseHistory };
}
