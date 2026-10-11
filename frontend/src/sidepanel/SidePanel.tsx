import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import Browser, { activityLabel, type BrowserState } from '../workspace/Browser';
import { runtime } from '../lib/api';
import { useProjectState, useRoomEvent, useRuntimeState } from '../lib/room';
import type { RuntimeState } from '../lib/types';
import { readStored, writeStored } from '../lib/storage';
import { legacy } from '../legacy';
import { usePanelTabs, type PanelRequest, type ResourceTab } from './usePanelTabs';
import NewPanelTab from './NewPanelTab';
import ChangesView from './ChangesView';
import ReviewFileView from './ReviewFileView';
import './sidepanel.css';

export type { PanelRequest } from './usePanelTabs';
type FileRenderer = (request: PanelRequest | null, active: boolean, close: () => void) => ReactNode;
type Props = {children: ReactNode; fileView?: FileRenderer};

export default function SidePanel({children, fileView}: Props) {
  const {data: room} = useRuntimeState();
  const [project, setProject] = useState(window.activeProjectId || '');
  useRoomEvent<{project_id?: string}>('settings', e => {if (e.data.project_id) setProject(e.data.project_id);});
  const key = window.activeProjectId || project || room?.project_id || room?.project || '';
  const [open, setOpen] = useState(() => readStored('workspace.companion.open') === '1');
  const [width, setWidth] = useState(() => Math.min(75, Math.max(30, Number(readStored('workspace.companion.width')) || 48)));
  const [expanded, setExpanded] = useState(false);
  const [hasResources, setHasResources] = useState(true);
  const hasResourcesRef = useRef(hasResources); hasResourcesRef.current = hasResources;
  const [launchRequest, setLaunchRequest] = useState(0);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const openRef = useRef(open); openRef.current = open;
  const [browserState, setBrowserState] = useState<BrowserState>();
  const currentProject = useRef(key); currentProject.current = key;
  const receiveBrowser = useCallback((state: BrowserState) => {
    if (!currentProject.current || state.project === currentProject.current) setBrowserState(state);
  }, []);
  useRoomEvent<BrowserState>('browser_activity', e => receiveBrowser(e.data));
  useEffect(() => {
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {const state = await runtime<BrowserState>('/browser?' + new URLSearchParams({project: key})); if (!stopped) receiveBrowser(state);} catch { /* Browser owns its errors. */ }
      finally {if (!stopped) timer = setTimeout(refresh, 2500);}
    }
    void refresh(); return () => {stopped = true; clearTimeout(timer);};
  }, [key, receiveBrowser]);
  useEffect(() => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'companion-toggle ws-icon-button';
    button.setAttribute('aria-label', 'Toggle side panel'); button.setAttribute('aria-keyshortcuts', 'Control+Shift+B');
    button.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.65" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg><i class="companion-ping" aria-hidden="true"></i>';
    const activate = () => {
      if (openRef.current) {setOpen(false); setExpanded(false); return;}
      if (!hasResourcesRef.current) setLaunchRequest(v => v + 1);
      setOpen(true);
    };
    toggle.current = button; button.onclick = activate;
    (legacy.header.querySelector('.header-right') || legacy.header).append(button);
    const shortcut = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {e.preventDefault(); activate();}
    };
    window.addEventListener('keydown', shortcut);
    return () => {toggle.current = null; button.remove(); window.removeEventListener('keydown', shortcut);};
  }, []);
  const currentBrowser = browserState?.project === key ? browserState : undefined;
  useEffect(() => {
    const button = toggle.current;
    if (!button) return;
    const activity = activityLabel(currentBrowser);
    button.classList.toggle('companion-active', !!currentBrowser?.running);
    button.setAttribute('aria-pressed', String(open));
    button.setAttribute('aria-label', open ? 'Hide side panel' : 'Show side panel');
    button.title = `${open ? 'Hide' : 'Show'} side panel (Ctrl+Shift+B)` + (currentBrowser?.running ? ` - ${activity}` : '');
    button.setAttribute('aria-description', activity);
  }, [currentBrowser, open, hasResources]);
  useEffect(() => {writeStored('workspace.companion.open', open ? '1' : '0'); window.dispatchEvent(new Event('resize'));}, [open]);
  useEffect(() => {writeStored('workspace.companion.width', String(width)); window.dispatchEvent(new Event('resize'));}, [width]);
  const hide = () => {setOpen(false); setExpanded(false); toggle.current?.focus();};
  return <div className="companion-layout">
    {children}
    <PanelResources key={key} project={key} room={room} browser={currentBrowser} onBrowser={receiveBrowser} open={open} show={() => setOpen(true)} hide={hide}
      width={width} setWidth={setWidth} expanded={expanded} setExpanded={setExpanded} fileView={fileView}
      launchRequest={launchRequest} onHasResources={setHasResources} />
  </div>;
}

function PanelResources({project, room, browser, onBrowser, open, show, hide, width, setWidth, expanded, setExpanded, fileView, launchRequest, onHasResources}: {
  project: string; room: RuntimeState | null; browser?: BrowserState; onBrowser: (state: BrowserState) => void; open: boolean; show: () => void; hide: () => void;
  width: number; setWidth: (width: number) => void; expanded: boolean; setExpanded: (expanded: boolean) => void; fileView?: FileRenderer;
  launchRequest: number; onHasResources: (value: boolean) => void;
}) {
  const tabs = usePanelTabs(project), {data: board, error: boardError} = useProjectState();
  const prefix = useId(), addButton = useRef<HTMLButtonElement>(null), expandButton = useRef<HTMLButtonElement>(null);
  const [announcement, setAnnouncement] = useState('');
  const [choosing, setChoosing] = useState(false);
  const chooserClose = useRef<HTMLButtonElement>(null), chooserOpener = useRef<HTMLElement | null>(null);
  const overflow = useRef<HTMLDetailsElement>(null);
  const [unavailable, setUnavailable] = useState<Record<string, boolean>>({});
  const lastLaunch = useRef(launchRequest);
  useEffect(() => {onHasResources(tabs.tabs.length > 0);}, [tabs.tabs.length, onHasResources]);
  useEffect(() => {
    if (lastLaunch.current === launchRequest) return;
    lastLaunch.current = launchRequest;
    chooserOpener.current = document.activeElement as HTMLElement;
    setChoosing(true); requestAnimationFrame(() => chooserClose.current?.focus());
  }, [launchRequest]);
  useEffect(() => {
    if (browser?.active_page && browser.pages?.some(p => p.id === browser.active_page)) tabs.bindDefaultBrowser(browser.active_page);
  }, [browser?.active_page, browser?.pages, tabs.bindDefaultBrowser]);
  const tabId = (id: string) => `${prefix}-tab-${encodeURIComponent(id)}`;
  const paneId = (id: string) => `${prefix}-pane-${encodeURIComponent(id)}`;
  useEffect(() => {document.getElementById(tabId(tabs.selected))?.scrollIntoView({block: 'nearest', inline: 'nearest'});}, [tabs.selected, tabs.tabs.length, prefix, width, expanded, open, choosing, unavailable]);
  const focusTab = (id: string) => requestAnimationFrame(() => {
    const button = document.getElementById(tabId(id));
    if (button) {button.focus(); button.scrollIntoView({block: 'nearest', inline: 'nearest'});} else addButton.current?.focus();
  });
  const openResource = (request: PanelRequest, focus = true) => {
    if (request.tab === 'browser' && !request.page && browser?.active_page) request = {...request, page: browser.active_page};
    const id = tabs.openResource(request); setChoosing(false); show(); setAnnouncement(`Opened ${request.tab} tab.`); if (focus) focusTab(id);
  };
  useEffect(() => {
    const listener = (event: Event) => {
      const request = (event as CustomEvent<PanelRequest>).detail;
      if (!request || !['file','browser','review','changes'].includes(request.tab || '')) return;
      event.preventDefault(); openResource(request, false);
    };
    window.addEventListener('workspace-side-panel', listener);
    return () => window.removeEventListener('workspace-side-panel', listener);
  });
  useEffect(() => {
    if (!open || !expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented && !(e.target as HTMLElement)?.closest('dialog[open], .browser-stage')) {
        e.preventDefault(); setExpanded(false); expandButton.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [expanded, open, setExpanded]);
  const identity = (tab: ResourceTab) => {
    if (tab.kind === 'browser') {
      const page = browser?.pages?.find(p => p.id === tab.request.page);
      return `Browser · ${project} · Page ${tab.request.page || 'current'} · ${page?.title || (!tab.request.page ? browser?.title : '') || 'Untitled'} · ${page?.url || ''}`;
    }
    if (tab.kind === 'file') return `${project} · ${tab.request.ref} · ${tab.request.source_label || tab.request.sender || 'Project'} · ${tab.request.at || 'Working copy'}`;
    if (tab.kind === 'review') {
      const review = board?.reviews?.find(r => r.id === tab.request.review_id), file = review?.snapshot?.files.find(f => f.path === tab.request.path);
      return `${project} · ${tab.request.path} · ${review?.revision || tab.request.review_id}${file ? ' · ' + file.sha256 : ''}`;
    }
    return `${project} · ${tab.label} · Current worktree diff`;
  };
  const isUnavailable = (tab: ResourceTab) => tab.kind === 'browser' && (!!unavailable[tab.id]
    || !!(tab.request.page && browser?.pages && !browser.pages.some(p => p.id === tab.request.page)));
  const baseLabel = (tab: ResourceTab) => tab.kind === 'browser'
    ? browser?.pages?.find(p => p.id === tab.request.page)?.title || (!tab.request.page ? browser?.title : '') || 'Untitled page'
    : tab.kind === 'review' ? String(tab.request.path || '').replace(/\\/g, '/').split('/').pop() || 'Review' : tab.label;
  const label = (tab: ResourceTab) => {
    const title = baseLabel(tab);
    if (tabs.tabs.filter(t => baseLabel(t) === title).length < 2) return title;
    let qualifier = String(tab.request.source_label || tab.request.sender || tab.request.at || tab.kind);
    if (tab.kind === 'browser') {try {qualifier = new URL(browser?.pages?.find(p => p.id === tab.request.page)?.url || '').host;} catch {qualifier = 'Page';}}
    return `${title} · ${qualifier || tab.kind} · ${tabs.tabs.filter(t => baseLabel(t) === title).findIndex(t => t.id === tab.id) + 1}`;
  };
  const accessibleLabel = (tab: ResourceTab) => `${label(tab)}${isUnavailable(tab) ? ' · Closed / unavailable' : ''} · ${identity(tab)}`;
  const select = (tab: ResourceTab) => {setChoosing(false); tabs.select(tab.id); setAnnouncement(`Selected ${label(tab)}.`);};
  const closeTab = (id: string) => {
    const index = tabs.tabs.findIndex(t => t.id === id), next = tabs.tabs[index + 1] ?? tabs.tabs[index - 1];
    const wasSelected = tabs.selected === id;
    if (index < 0) return;
    setAnnouncement(`Closed ${label(tabs.tabs[index])} viewer.`); tabs.closeTab(id);
    if (tabs.tabs.length === 1) {setChoosing(false); hide();}
    else focusTab(wasSelected ? next?.id || '' : tabs.selected);
  };
  const chooser = () => {chooserOpener.current = document.activeElement as HTMLElement; setChoosing(true); show(); requestAnimationFrame(() => chooserClose.current?.focus()); setAnnouncement('Choose what to open beside chat.');};
  const cancelChooser = () => {setChoosing(false); if (tabs.tabs.length) requestAnimationFrame(() => chooserOpener.current?.isConnected ? chooserOpener.current.focus() : focusTab(tabs.selected)); else hide();};
  return <aside hidden={!open} className={`companion-panel${expanded ? ' companion-expanded' : ''}`} style={{width: `${width}%`}} aria-label="Workspace side panel">
    <div className="companion-resize" role="separator" tabIndex={0} aria-label="Resize side panel" aria-orientation="vertical" aria-valuemin={30} aria-valuemax={75} aria-valuenow={Math.round(width)}
      onKeyDown={e => {if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {e.preventDefault(); setWidth(Math.max(30, Math.min(75, width + (e.key === 'ArrowLeft' ? 3 : -3))));}}}
      onPointerDown={e => e.currentTarget.setPointerCapture(e.pointerId)}
      onPointerMove={e => {if (e.currentTarget.hasPointerCapture(e.pointerId)) {const box=e.currentTarget.parentElement!.parentElement!.getBoundingClientRect(); setWidth(Math.max(30, Math.min(75, (box.right-e.clientX)/box.width*100)));}}} />
    <div className="companion-tabbar">
      <div className="companion-tabs" role="tablist" aria-label="Open workspace resources">
        {tabs.tabs.map(tab => <div className="companion-tab" role="presentation" key={tab.id}>
          <button id={tabId(tab.id)} role="tab" aria-selected={tabs.selected === tab.id} aria-controls={paneId(tab.id)} tabIndex={tabs.selected === tab.id ? 0 : -1}
            title={accessibleLabel(tab)} aria-label={accessibleLabel(tab)}
            onClick={() => select(tab)}
            onKeyDown={e => {
              if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) {
                e.preventDefault(); const index=tabs.tabs.findIndex(t => t.id === tab.id);
                const next=e.key === 'Home' ? 0 : e.key === 'End' ? tabs.tabs.length-1 : (index+(e.key === 'ArrowRight' ? 1 : -1)+tabs.tabs.length)%tabs.tabs.length;
                select(tabs.tabs[next]); focusTab(tabs.tabs[next].id);
              } else if (e.key === 'Delete') {e.preventDefault(); closeTab(tab.id);}
            }}><span aria-hidden="true" className="companion-kind">{({browser:'◎',file:'▧',review:'✓',changes:'±'})[tab.kind]}</span><span className="companion-tab-title">{label(tab)}</span>{isUnavailable(tab) && <span className="companion-tab-state">Closed</span>}</button>
          <button className="companion-tab-close" tabIndex={-1} aria-label={`Close ${accessibleLabel(tab)} viewer`} onClick={() => closeTab(tab.id)}>×</button>
        </div>)}
      </div>
      <div className="companion-tab-actions">
        <button ref={addButton} aria-label="New workspace tab" title="New tab" onClick={chooser}>+</button>
        <details className="companion-overflow" ref={overflow} onKeyDown={e => {if (e.key === 'Escape') {e.preventDefault(); e.stopPropagation(); e.currentTarget.open = false; e.currentTarget.querySelector('summary')?.focus();}}}>
          <summary aria-label="All open resources" title="All open resources">⌄</summary><div className="companion-resource-list">
          {tabs.tabs.length ? tabs.tabs.map(tab => <button key={tab.id} aria-label={accessibleLabel(tab)} onClick={() => {select(tab); if (overflow.current) overflow.current.open = false; focusTab(tab.id);}}><strong>{label(tab)}{isUnavailable(tab) && ' · Closed / unavailable'}</strong><small>{identity(tab)}</small></button>) : <p>No viewers open.</p>}
          </div>
        </details>
        <button className="companion-expand" ref={expandButton} aria-label={expanded ? 'Restore panel width' : 'Expand panel'} title={expanded ? 'Restore' : 'Expand'} onClick={() => setExpanded(!expanded)}>{expanded ? '↙' : '↗'}</button>
      </div>
    </div>
    <p className="panel-sr-only" role="status">{announcement}</p>
    {tabs.tabs.map(tab => {
      const active = open && !choosing && tabs.selected === tab.id;
      const renderers: Record<ResourceTab['kind'], () => ReactNode> = {
        browser: () => <Browser active={active} project={project} page={typeof tab.request.page === 'string' ? tab.request.page : undefined} onState={onBrowser} choosePage={chooser} closeViewer={() => closeTab(tab.id)} onUnavailable={value => setUnavailable(previous => previous[tab.id] === value ? previous : {...previous, [tab.id]: value})} />,
        file: () => fileView ? fileView(tab.request, active, hide) : <p className="companion-empty">File preview is unavailable.</p>,
        review: () => <ReviewFileView project={project} request={tab.request} active={active} review={board?.reviews?.find(r => r.id === tab.request.review_id)} />,
        changes: () => <ChangesView active={active} agent={tab.request.agent ? String(tab.request.agent) : undefined} />,
      };
      return <div id={paneId(tab.id)} key={tab.id} className="companion-body" hidden={choosing || tabs.selected !== tab.id} role="tabpanel" aria-labelledby={tabId(tab.id)}>{renderers[tab.kind]()}</div>;
    })}
    {(choosing || !tabs.tabs.length) && <section className="companion-body companion-chooser" aria-label="Open beside chat" onKeyDown={e => {if (e.key === 'Escape' && !e.defaultPrevented) {e.preventDefault(); e.stopPropagation(); cancelChooser();}}}>
      <button className="companion-chooser-close" ref={chooserClose} onClick={cancelChooser}>{tabs.tabs.length ? 'Back to tabs' : 'Close side panel'}</button>
      {boardError && <p className="panel-error" role="alert">Could not load project reviews: {boardError}. Other resources remain available.</p>}
      <NewPanelTab project={board} projectId={project} room={room} browser={browser} tabs={tabs.tabs} onBrowser={onBrowser} open={openResource} />
    </section>}
  </aside>;
}
