/* The workspace view and focused project, kept in the URL (?view=, ?project=)
 * so reloads, links and the browser Back button land on the same page. */
import { useCallback, useEffect, useState } from 'react';
import { EVENTS } from '../lib/constants';
import { legacy } from '../legacy';

export const VIEWS = ['projects', 'chat', 'board', 'team', 'activity'] as const;
export type View = typeof VIEWS[number];

const DEFAULT_VIEW: View = 'chat';
const validView = (value: string | null): View => (VIEWS.includes(value as View) ? (value as View) : DEFAULT_VIEW);
const param = (name: string) => new URL(location.href).searchParams.get(name);

export function useWorkspaceRoute() {
  const [view, setView] = useState<View>(() => validView(param('view')));
  const [focus, setFocusState] = useState(() => param('project') || '');

  const navigate = useCallback((next: View) => {
    setView(next);
    const url = new URL(location.href);
    url.searchParams.set('view', next);
    history.pushState({}, '', url);
  }, []);

  // Focusing a project replaces the history entry: it refines the page rather than leaving it.
  const setFocus = useCallback((id: string) => {
    setFocusState(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set('project', id);
    else url.searchParams.delete('project');
    history.replaceState({}, '', url);
  }, []);

  useEffect(() => {
    // Back/Forward: the entry's URL is the source of truth for both the view and the project.
    const back = () => {
      setView(validView(param('view')));
      setFocusState(param('project') || '');
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

  return { view, navigate, focus, setFocus };
}
