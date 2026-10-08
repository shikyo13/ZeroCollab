import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import Browser, { activityLabel, type BrowserState } from '../workspace/Browser';
import { runtime } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import { readStored, writeStored } from '../lib/storage';
import { legacy } from '../legacy';
import './sidepanel.css';

export type PanelRequest = Record<string, unknown> & {tab?: 'file' | 'browser'};
type Props = {children: ReactNode; fileView?: (request: PanelRequest | null, active: boolean, close: () => void) => ReactNode};

export default function SidePanel({children, fileView}: Props) {
  const [open, setOpen] = useState(() => readStored('workspace.companion.open') === '1');
  const [tab, setTab] = useState<'file' | 'browser'>('browser');
  const [request, setRequest] = useState<PanelRequest | null>(null);
  const [width, setWidth] = useState(() => Math.min(75, Math.max(30, Number(readStored('workspace.companion.width')) || 48)));
  const [expanded, setExpanded] = useState(false);
  const [browserState, setBrowserState] = useState<BrowserState>();
  const browserButton = useRef<HTMLButtonElement | null>(null);
  const seenRuns = useRef(new Set<string>());
  const receiveBrowser = useCallback((state: BrowserState) => {
    setBrowserState(state);
    const activity = state.activity;
    if (!activity || activity.actor === 'user' || ['release', 'stop', 'status'].includes(activity.action)) return;
    const recent = Date.now() / 1000 - activity.time < 20 || state.owner === activity.actor;
    const key = `${state.project}:${activity.actor}`;
    if (recent && !seenRuns.current.has(key) && (state.running || ['claim', 'open'].includes(activity.action))) {
      seenRuns.current.add(key); setTab('browser'); setOpen(true);
    }
  }, []);
  useRoomEvent<BrowserState>('browser_activity', event => receiveBrowser(event.data));
  useEffect(() => {
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {const state = await runtime<BrowserState>('/browser'); if (!stopped) receiveBrowser(state);} catch { /* existing panel shows request errors */ }
      finally {if (!stopped) timer = setTimeout(refresh, 2500);}
    }
    void refresh();
    return () => {stopped = true; clearTimeout(timer);};
  }, [receiveBrowser]);
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<PanelRequest>).detail;
      if (!detail || !['file', 'browser'].includes(detail.tab || '')) return;
      event.preventDefault(); // Tell the legacy link handler that the panel handled this reference.
      setTab(detail.tab!); setOpen(true);
      if (detail.tab === 'file') setRequest(detail);
    };
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'companion-open'; button.textContent = 'Open browser';
    button.setAttribute('aria-label', 'Open shared browser beside conversation');
    browserButton.current = button;
    button.onclick = () => {setTab('browser'); setOpen(true);};
    (legacy.header.querySelector('.header-right') || legacy.header).prepend(button);
    window.addEventListener('workspace-side-panel', listener);
    return () => {browserButton.current = null; button.remove(); window.removeEventListener('workspace-side-panel', listener);};
  }, []);
  useEffect(() => {
    if (!browserButton.current) return;
    browserButton.current.textContent = activityLabel(browserState);
    browserButton.current.classList.toggle('companion-active', !!browserState?.running);
    browserButton.current.title = 'Show the shared browser beside the conversation';
  }, [browserState]);
  useEffect(() => {writeStored('workspace.companion.open', open ? '1' : '0'); window.dispatchEvent(new Event('resize'));}, [open]);
  useEffect(() => {writeStored('workspace.companion.width', String(width)); window.dispatchEvent(new Event('resize'));}, [width]);
  const close = () => {setOpen(false); setExpanded(false);};
  return <div className="companion-layout">
    {children}
    {open && <aside className={`companion-panel${expanded ? ' companion-expanded' : ''}`} style={{width: `${width}%`}} aria-label="Conversation companion panel">
      <div className="companion-resize" role="separator" tabIndex={0} aria-label="Resize companion panel" aria-orientation="vertical" aria-valuemin={30} aria-valuemax={75} aria-valuenow={Math.round(width)}
        onKeyDown={e => {if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {e.preventDefault(); setWidth(w => Math.max(30, Math.min(75, w + (e.key === 'ArrowLeft' ? 3 : -3))));}}}
        onPointerDown={e => {e.currentTarget.setPointerCapture(e.pointerId);}}
        onPointerMove={e => {if (e.currentTarget.hasPointerCapture(e.pointerId)) {const box=e.currentTarget.parentElement!.parentElement!.getBoundingClientRect(); setWidth(Math.max(30, Math.min(75, (box.right-e.clientX)/box.width*100)));}}} />
      <div className="companion-tabs" role="tablist" aria-label="Companion views">
        <button role="tab" aria-selected={tab === 'browser'} onClick={() => setTab('browser')}>Browser</button>
        <button role="tab" aria-selected={tab === 'file'} onClick={() => setTab('file')}>File</button>
        <span />
        <button aria-label={expanded ? 'Restore panel width' : 'Expand panel'} title={expanded ? 'Restore' : 'Expand'} onClick={() => setExpanded(v => !v)}>{expanded ? '↙' : '↗'}</button>
        <button aria-label="Close companion panel" onClick={close}>×</button>
      </div>
      <div className="companion-body" hidden={tab !== 'browser'} role="tabpanel" aria-label="Browser"><Browser active={open && tab === 'browser'} /></div>
      <div className="companion-body" hidden={tab !== 'file'} role="tabpanel" aria-label="File">
        {fileView ? fileView(request, open && tab === 'file', close) : <p className="companion-empty">Click a file reference in chat to open it here.</p>}
      </div>
    </aside>}
  </div>;
}
