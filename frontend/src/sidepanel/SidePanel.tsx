import { useEffect, useState, type ReactNode } from 'react';
import Browser from '../workspace/Browser';
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
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<PanelRequest>).detail;
      if (!detail || !['file', 'browser'].includes(detail.tab || '')) return;
      setTab(detail.tab!); setOpen(true);
      if (detail.tab === 'file') setRequest(detail);
    };
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'companion-open'; button.textContent = 'Open browser';
    button.setAttribute('aria-label', 'Open shared browser beside conversation');
    button.onclick = () => {setTab('browser'); setOpen(true);};
    (legacy.header.querySelector('.header-right') || legacy.header).prepend(button);
    window.addEventListener('workspace-side-panel', listener);
    return () => {button.remove(); window.removeEventListener('workspace-side-panel', listener);};
  }, []);
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
