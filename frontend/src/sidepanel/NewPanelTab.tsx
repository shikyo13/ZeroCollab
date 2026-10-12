import { useEffect, useId, useRef, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import type { BrowserState } from '../workspace/Browser';
import type { ProjectState, RuntimeState } from '../lib/types';
import type { PanelRequest, ResourceTab } from './usePanelTabs';

export default function NewPanelTab({project, projectId, room, browser, tabs, onBrowser, open}: {
  project: ProjectState | null; projectId: string; room: RuntimeState | null; browser?: BrowserState; tabs: ResourceTab[];
  onBrowser: (state: BrowserState) => void; open: (request: PanelRequest) => void;
}) {
  const [mode, setMode] = useState<'file' | 'review' | ''>('');
  const [ref, setRef] = useState(''), [sender, setSender] = useState(''), [at, setAt] = useState('');
  const [reviewId, setReviewId] = useState(''), [path, setPath] = useState(''), [error, setError] = useState('');
  const reviews = (project?.reviews ?? []).filter(r => r.snapshot?.files.length);
  const review = reviews.find(r => r.id === reviewId);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const creationReason = useId();
  const pages = browser?.pages ?? [];
  const controlling = browser?.owner === 'user';
  const atCap = pages.length >= (browser?.max_pages ?? 4);
  const create = async () => {
    setBusy(true); setError('');
    try {
      const next = await runtime<BrowserState>('/browser', {action: 'new_page', project: projectId, browser: browser?.browser});
      if (!alive.current) return;
      if (next.project !== projectId || !next.page) throw new Error('No shared page was created for this project. Refresh and try again.');
      onBrowser(next); open({tab: 'browser', page: next.page});
    } catch (e) {if (alive.current) setError(errorMessage(e));} finally {if (alive.current) setBusy(false);}
  };
  const claim = async () => {
    setBusy(true); setError('');
    try {const next = await runtime<BrowserState>('/browser', {action: 'claim', project: projectId, browser: browser?.browser}); if (!alive.current) return; if (next.project !== projectId) throw new Error('Project changed. Reopen this panel.'); onBrowser(next);}
    catch (e) {if (alive.current) setError(errorMessage(e));} finally {if (alive.current) setBusy(false);}
  };
  return <div className="panel-new">
    <p className="panel-eyebrow">WORKSPACE</p><h2>Open beside chat</h2><p>Choose a file, review, changes or a shared browser page.</p>
    <div className="panel-choices">
      <button onClick={() => open({tab: 'browser', watch_agents: true})}><strong>Shared browser</strong><small>Watch or select an agent browser</small></button>
      <button onClick={() => {setMode('file'); setError('');}}><strong>File</strong><small>Read a project or agent file</small></button>
      <button onClick={() => {setMode('review'); setError('');}}><strong>File review</strong><small>The exact version a teammate sent for review</small></button>
      <button onClick={() => open({tab: 'changes'})}><strong>Changes</strong><small>What teammates have edited</small></button>
    </div>
    {browser?.pages !== undefined ? <section className="panel-browser-pages" aria-label="Shared browser pages">
      <h3>Shared browser pages · {pages.length} / {browser.max_pages ?? 4}</h3>
      {pages.map(page => <button key={page.id} className="panel-page-choice" onClick={() => open({tab: 'browser', page: page.id})}>
        <strong>{page.title || 'Untitled page'}</strong><small>{page.url || 'about:blank'} · Page {page.id}</small>
        <small>{browser.active_page === page.id && 'Team active · '}{tabs.some(t => t.request.page === page.id) ? 'Viewer open' : 'Open viewer'}</small>
      </button>)}
      {!pages.length && <p>No shared pages are open.</p>}
      <div className="panel-actions"><button className="panel-primary" disabled={!controlling || atCap || busy} aria-describedby={creationReason} onClick={() => void create()}>New browser page</button>
        {!controlling && <button disabled={!browser || busy} onClick={() => void claim()}>Take control</button>}</div>
      <p className="panel-muted" id={creationReason}>{atCap ? 'Page limit reached. Close an actual shared page to make room; closing a viewer does not free a page.' : controlling ? 'Creates a shared page and makes it active for the team.' : 'Take control to create a page. Existing pages remain available to view.'}</p>
      <p className="panel-muted">{controlling ? 'You control the browser' : browser.owner ? `${browser.owner_label} controls the browser` : 'Browser available to control'}. Viewing or closing a tab leaves its page and the team’s active page unchanged.</p>
    </section> : <p className="panel-muted">Closing the browser view leaves it running.</p>}
    {mode === 'file' && <form className="panel-form" onSubmit={e => {
      e.preventDefault();
      if (!ref.trim()) {setError('Enter a file path.'); return;}
      if (at.trim() && !/^[a-f\d]{7,40}$/i.test(at.trim())) {setError('Use a Git commit ID of 7 to 40 hexadecimal characters.'); return;}
      setError(''); open({tab: 'file', ref: ref.trim(), sender, ...(at.trim() ? {at: at.trim()} : {})});
    }}>
      <h3>Open file</h3><label>File path<input value={ref} onChange={e => setRef(e.target.value)} placeholder="frontend/src/App.tsx:12" required maxLength={2000} /></label>
      <label>Code copy<select value={sender} onChange={e => setSender(e.target.value)}><option value="">Project repositories</option>{Object.entries(room?.agents ?? {}).filter(([,a]) => !a.archived).map(([id,a]) => <option key={id} value={id}>{a.label || id}'s worktree</option>)}</select></label>
      <label>Git revision (optional)<input value={at} onChange={e => setAt(e.target.value)} placeholder="Working copy when empty" maxLength={40} /></label>
      <p className="panel-muted">Ambiguous paths ask you to choose a match. Working copies are labelled separately from submitted review snapshots.</p>
      <div className="panel-actions"><button type="submit" className="panel-primary">Open file</button><button type="button" onClick={() => setMode('')}>Cancel</button></div>
    </form>}
    {mode === 'review' && <form className="panel-form" onSubmit={e => {
      e.preventDefault(); if (!review || !path) {setError('Choose a review and a captured file.'); return;}
      setError(''); open({tab: 'review', review_id: review.id, path});
    }}>
      <h3>Open captured file review</h3>
      {reviews.length ? <><label>Review<select value={reviewId} required onChange={e => {setReviewId(e.target.value); setPath('');}}><option value="">Choose a review</option>{reviews.map(r => <option key={r.id} value={r.id}>{r.id} · {r.revision} · {r.status}</option>)}</select></label>
        <label>Captured file<select value={path} required onChange={e => setPath(e.target.value)}><option value="">Choose a captured file</option>{review?.snapshot?.files.map(f => <option key={f.path} value={f.path}>{f.path} · {f.sha256.slice(0,12)}</option>)}</select></label>
        <div className="panel-actions"><button type="submit" className="panel-primary">Open file review</button><button type="button" onClick={() => setMode('')}>Cancel</button></div></>
        : <p>No reviews with captured files in this project.</p>}
    </form>}
    {error && <p className="panel-error" role="alert">{error}</p>}
  </div>;
}
