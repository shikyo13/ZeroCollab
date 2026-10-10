import { useCallback, useEffect, useState } from 'react';
import { readStored, writeStored } from '../lib/storage';

export type PanelRequest = Record<string, unknown> & {tab?: 'file' | 'browser' | 'review' | 'changes'};
export type ResourceKind = 'browser' | 'file' | 'review' | 'changes';
export type ResourceTab = {id: string; kind: ResourceKind; label: string; request: PanelRequest};
const initial: ResourceTab[] = [{id: 'browser', kind: 'browser', label: 'Shared browser', request: {tab: 'browser'}}];

function resource(request: PanelRequest): ResourceTab {
  const kind = request.tab!;
  if (kind === 'browser') return typeof request.page === 'string' && request.page
    ? {id: 'browser:' + request.page, kind, label: 'Browser', request: {tab: 'browser', page: request.page}}
    : initial[0];
  const ref = String(request.ref ?? '');
  const label = kind === 'file' ? ref.replace(/\\/g, '/').split('/').pop() || 'File'
    : kind === 'review' ? `Review · ${String(request.path ?? '').replace(/\\/g, '/').split('/').pop() || request.review_id}`
    : request.agent ? `Changes · ${request.agent}` : 'Changes';
  const identity = kind === 'file' ? [ref.replace(/(?::\d+(?:-\d+|:\d+)?|#L\d+(?:-L?\d+)?)$/, ''), request.sender ?? '', request.root ?? '', request.at ?? '']
    : kind === 'review' ? [request.review_id, request.path] : [request.agent ?? ''];
  return {id: kind + ':' + JSON.stringify(identity), kind, label, request};
}

/** This state is mounted with a project key. Views and drafts survive tab/panel hiding,
 * but never follow a switch to another project's repositories. No file bytes are persisted. */
export function usePanelTabs(project: string) {
  const key = 'workspace.companion.tabs:' + project;
  const [saved] = useState(() => {
    try {
      const data = JSON.parse(readStored(key) || 'null');
      if (!data || !Array.isArray(data.tabs)) return null;
      const tabs: ResourceTab[] = [];
      for (const raw of data.tabs) {
        // Old versions stored the chooser as a tab. It is now transient UI only.
        if (raw?.tab === 'new') continue;
        if (!raw || !['file', 'browser', 'review', 'changes'].includes(raw.tab)) continue;
        const request: PanelRequest = {tab: raw.tab};
        // Store descriptors only. Root indices are transient: reopening must choose their source again.
        for (const field of ['ref','sender','at','review_id','path','agent','page']) if (typeof raw[field] === 'string') request[field] = raw[field].slice(0, 2000);
        if (raw.source_required === true) request.source_required = true;
        if (typeof raw.time === 'number') request.time = raw.time;
        const next = resource(request);
        if (!tabs.some(t => t.id === next.id)) tabs.push(next);
      }
      return {tabs, selected: typeof data.selected === 'string' ? data.selected : ''};
    } catch {return null;}
  });
  const [tabs, setTabs] = useState<ResourceTab[]>(saved?.tabs ?? initial);
  const [selected, setSelected] = useState(saved?.tabs.find(t => t.id === saved.selected)?.id ?? saved?.tabs[0]?.id ?? (saved ? '' : 'browser'));
  useEffect(() => {
    if (!project) return;
    const descriptors = tabs.map(t => {
      const r = t.request, safe: PanelRequest = {tab: r.tab};
      for (const field of ['ref','sender','at','review_id','path','agent','time','page']) if (r[field] !== undefined) safe[field] = r[field];
      if (r.root != null || r.source_required) safe.source_required = true;
      return safe;
    });
    writeStored(key, JSON.stringify({tabs: descriptors, selected}));
  }, [key, project, tabs, selected]);
  const openResource = useCallback((request: PanelRequest, select = true) => {
    const next = resource(request);
    setTabs(tabs => tabs.some(t => t.id === next.id) ? tabs.map(t => t.id === next.id ? next : t) : [...tabs, next]);
    if (select) setSelected(next.id);
    return next.id;
  }, []);
  // Bind only the legacy/default viewer once. A stale saved page never follows a newer active page.
  const bindDefaultBrowser = useCallback((page: string) => {
    const next = resource({tab: 'browser', page});
    setTabs(tabs => tabs.some(t => t.id === 'browser') ? tabs.flatMap(t => t.id === 'browser' ? tabs.some(t => t.id === next.id) ? [] : [next] : [t]) : tabs);
    setSelected(id => id === 'browser' ? next.id : id);
  }, []);
  const closeTab = (id: string) => {
    const index = tabs.findIndex(t => t.id === id);
    const rest = tabs.filter(t => t.id !== id);
    setTabs(rest);
    if (selected === id) setSelected(rest[Math.min(index, rest.length - 1)]?.id ?? '');
  };
  return {tabs, selected, select: setSelected, openResource, closeTab, bindDefaultBrowser};
}
