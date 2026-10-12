import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError, runtime, sessionHeaders, errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import './browser.css';

export type BrowserPage = {id: string; url: string; title: string; generation: number; dialog: boolean; file_chooser: boolean; active: boolean};
export type BrowserState = {
  running: boolean; project: string; url: string; title: string; owner: string; owner_label: string;
  browser?: string; agent?: string; browser_label?: string; generation?: number;
  instances?: {id: string; agent: string; label: string; running: boolean; pages: number; owner: string}[];
  width: number; height: number; lease_seconds: number | null;
  page?: string; active_page?: string; pages?: BrowserPage[]; max_pages?: number;
  events: {time: number; kind: string; text: string; page?: string}[];
  dialog: {type: string; message: string} | null; file_chooser: boolean;
  activity?: {actor: string; label: string; action: string; phase: string; time: number; page?: string} | null;
};
type InputTarget = {project: string; browser: string; page: string; generation?: number; selection: number};

export function activityLabel(state?: BrowserState): string {
  if (!state) return 'Browser';
  const a = state.activity;
  const actions: Record<string, string> = {claim: 'Taking control', open: 'Opening page', inspect: 'Reading page', screenshot: 'Viewing page', click: 'Clicking', type: 'Typing', key: 'Using keyboard', scroll: 'Scrolling', select: 'Selecting', reload: 'Reloading', dialog: 'Answering dialog', release: 'Released control', stop: 'Stopped browser'};
  if (a && Date.now() / 1000 - a.time < 30) {
    const action = actions[a.action] || 'Using browser';
    return `${a.label} · ${a.phase === 'failed' ? 'Action failed' : a.phase === 'working' ? action : 'Last: ' + action.toLowerCase()}`;
  }
  return state.owner === 'user' ? 'You control the browser' : state.owner ? `${state.owner_label} controls the browser` : 'Browser available to control';
}

export default function Browser({ active, project, page, onState, choosePage, closeViewer, onUnavailable }: {
  active: boolean; project?: string; page?: string; onState?: (state: BrowserState) => void; choosePage?: () => void; closeViewer?: () => void; onUnavailable?: (value: boolean) => void;
}) {
  const [state, setState] = useState<BrowserState>();
  const [selectedBrowser, setSelectedBrowser] = useState('');
  const [visible, setVisible] = useState(!document.hidden);
  const [address, setAddress] = useState('http://localhost:5173');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [frame, setFrame] = useState('');
  const [text, setText] = useState('');
  const [prompt, setPrompt] = useState('');
  const [closed, setClosed] = useState(false);
  const [fit, setFit] = useState(true);
  const menu = useRef<HTMLDetailsElement>(null), addressInput = useRef<HTMLInputElement>(null), ownerButton = useRef<HTMLButtonElement>(null);
  const shutdown = useRef<HTMLDialogElement>(null), textTool = useRef<HTMLDialogElement>(null);
  const unavailableCallback = useRef(onUnavailable); unavailableCallback.current = onUnavailable;
  useEffect(() => {unavailableCallback.current?.(closed);}, [closed]);
  const confirm = useRef<HTMLDialogElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  const recoveryButton = useRef<HTMLButtonElement>(null);
  const confirmationId = useId();
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const image = useRef<HTMLImageElement>(null);
  const imageMounted = useRef(false);
  const stateRef = useRef(state);
  const inputQueue = useRef<Promise<unknown>>(Promise.resolve());
  const selection = useRef(0);
  const viewer = useRef({active, visible, project, page});
  viewer.current = {active, visible, project, page};
  const dragging = useRef(false);
  const lastMove = useRef(0);
  const addressEditing = useRef(false);
  const canonicalPending = useRef(false);
  const refreshFrame = useRef<() => void>(() => undefined);
  stateRef.current = state;
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  const controlling = state?.owner === 'user';
  const events = state?.events.filter(event => !page || !event.page || event.page === page) ?? [];
  const pageActivity = page && state?.activity?.page && state.activity.page !== page ? {...state, activity: null} : state;
  const ownerLabel = closed ? 'Shared page closed or unavailable' : controlling ? 'You control the browser' : state?.owner ? `${state.owner_label} controls the browser` : state ? 'Browser available to control' : 'Checking browser availability';
  const currentAction = pageActivity?.activity && pageActivity.activity.phase === 'working' && Date.now() / 1000 - pageActivity.activity.time < 30 ? activityLabel(pageActivity) : '';
  useEffect(() => {if (state?.url && !addressEditing.current) setAddress(state.url);}, [state?.url]);
  useRoomEvent<BrowserState>('browser_activity', event => {
    if (project && event.data.project !== project) return;
    // Even another page's event may change the global lease. Read our own page instead of adopting it.
    refreshFrame.current();
  });

  useEffect(() => {
    if (!active || !visible) return;
    let stopped = false, polling = false, refreshPending = false, timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    async function poll() {
      if (polling) {refreshPending = true; return;}
      polling = true;
      try {
        const query = new URLSearchParams({...project ? {project} : {}, ...page ? {page} : {}, ...selectedBrowser && !page ? {browser: selectedBrowser} : {}});
        const next = await runtime<BrowserState>('/browser?' + query);
        if (stopped) return;
        if (project && next.project !== project) {setState(undefined); setFrame(''); setError('Project changed; reopen the shared browser for this project.'); return;}
        if (page && next.page !== page) throw new ApiError('This shared browser page has closed or is unavailable.', 400);
        setState(next); setClosed(false); onState?.(next);
        if (!page && !selectedBrowser && next.running && next.browser) setSelectedBrowser(next.browser);
        if (canonicalPending.current) {canonicalPending.current = false; setAddress(next.url || 'about:blank');}
        if (!next.running) setFrame('');
      } catch (e) { if (!stopped) {
        setError(errorMessage(e)); setFrame('');
        if (e instanceof ApiError && (e.status === 400 || e.status === 409)) {setState(undefined); setClosed(e.status === 400 && !!page);}
      } }
      finally { polling = false; if (!stopped) {timer = setTimeout(poll, refreshPending ? 0 : 3000); refreshPending = false;} }
    }
    refreshFrame.current = () => {clearTimeout(timer); void poll();};
    void poll();
    return () => { stopped = true; refreshFrame.current = () => undefined; abort.abort(); clearTimeout(timer); };
  }, [active, visible, project, page, selectedBrowser, onState]);

  // Multipart frames arrive independently of status and actions. Keep only the
  // newest frame per animation tick; a slow viewer never builds a frame backlog.
  const currentBrowser = state?.browser, currentPage = state?.page, currentProject = state?.project;
  const running = state?.running;
  useEffect(() => {
    if (!active || !visible || !running || !currentBrowser || !currentPage || !currentProject) return;
    const abort = new AbortController();
    let objectUrl = '', buffered = new Uint8Array(0), latest: Uint8Array | null = null, raf = 0;
    let retry: ReturnType<typeof setTimeout> | undefined, attempts = 0;
    imageMounted.current = false; setFrame('');
    function present() {
      raf = 0;
      if (!latest || abort.signal.aborted) return;
      const fresh = URL.createObjectURL(new Blob([latest as Uint8Array<ArrayBuffer>], {type: 'image/jpeg'}));
      latest = null;
      if (image.current && imageMounted.current) image.current.src = fresh;
      else {setFrame(fresh); imageMounted.current = true;}
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = fresh;
    }
    async function read() {
      if (abort.signal.aborted) return;
      buffered = new Uint8Array(0);
      let received = false;
      try {
        const query = new URLSearchParams({project: currentProject!, browser: currentBrowser!, page: currentPage!});
        const response = await fetch('/api/runtime/browser/stream?' + query, {headers: sessionHeaders(), signal: abort.signal});
        if (!response.ok || !response.body) throw new Error('Could not receive this browser stream.');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        try {
          while (!abort.signal.aborted) {
            const chunk = await reader.read();
            if (chunk.done) break;
            const combined = new Uint8Array(buffered.length + chunk.value.length);
            combined.set(buffered); combined.set(chunk.value, buffered.length); buffered = combined;
            if (buffered.length > 8 * 1024 * 1024) throw new Error('Browser frame exceeded the viewing limit.');
            while (buffered.length) {
              let end = -1;
              for (let i = 0; i + 3 < buffered.length; i++) {
                if (buffered[i] === 13 && buffered[i + 1] === 10 && buffered[i + 2] === 13 && buffered[i + 3] === 10) {end = i; break;}
              }
              if (end < 0) break;
              const header = decoder.decode(buffered.subarray(0, end));
              const match = /Content-Length:\s*(\d+)/i.exec(header);
              if (!match) throw new Error('Invalid browser frame stream.');
              const length = Number(match[1]);
              if (length < 1 || length > 8 * 1024 * 1024) throw new Error('Invalid browser frame size.');
              if (buffered.length < end + 4 + length + 2) break;
              latest = buffered.slice(end + 4, end + 4 + length);
              if (!received) {received = true; attempts = 0; setError('');}
              buffered = buffered.slice(end + 4 + length + 2);
              if (!raf) raf = requestAnimationFrame(present);
            }
          }
          if (!abort.signal.aborted) throw new Error('The browser stream disconnected.');
        } finally {await reader.cancel().catch(() => undefined); reader.releaseLock();}
      } catch (e) {
        if (!abort.signal.aborted) {
          setError(errorMessage(e) + ' Reconnecting…');
          // One connection and one timer per visible viewer; retry delay is capped.
          retry = setTimeout(() => void read(), Math.min(5000, 1000 * 2 ** Math.min(attempts++, 3)));
        }
      }
    }
    void read();
    return () => {abort.abort(); clearTimeout(retry); if (raf) cancelAnimationFrame(raf); if (objectUrl) URL.revokeObjectURL(objectUrl); imageMounted.current = false; setFrame('');};
  }, [active, visible, running, currentBrowser, currentPage, currentProject, state?.generation]);

  const send = useCallback(async (action: string, args: Record<string, unknown> = {}, target?: InputTarget) => {
    const current = stateRef.current;
    if (!current || !alive.current) return;
    const scope = viewer.current;
    if (target && (!scope.active || !scope.visible || target.selection !== selection.current
        || target.project !== current.project || target.browser !== current.browser || target.page !== current.page
        || target.generation !== current.generation || scope.project && scope.project !== target.project
        || scope.page && scope.page !== target.page || current.owner !== 'user')) return;
    const chosen = selection.current;
    try {
      const next = await runtime<BrowserState>('/browser', {action, ...args, project: target?.project || project || current.project,
        browser: target?.browser || current.browser, ...target ? {page: target.page} : page ? {page} : {}});
      if (!alive.current || chosen !== selection.current || viewer.current.project !== scope.project
          || viewer.current.page !== scope.page || stateRef.current?.browser !== current.browser) return;
      onState?.(next);
      // Global lease/stop/close commands can return the team-active page. Never put that page in this viewer.
      if ((!project || next.project === project) && (!page || next.page === page)) setState(next);
      if (action === 'close_page' && page) {setState(undefined); setClosed(true); setFrame(''); requestAnimationFrame(() => recoveryButton.current?.focus());}
      setError(''); refreshFrame.current();
      return true;
    } catch (e) {
      if (alive.current && chosen === selection.current && stateRef.current?.browser === current.browser
          && viewer.current.project === scope.project && viewer.current.page === scope.page) setError(errorMessage(e));
    }
  }, [project, page, onState]);
  const command = async (action: string, args: Record<string, unknown> = {}) => {
    setBusy(true); const ok = await send(action, args); setBusy(false); return ok;
  };
  const closeMenu = () => {if (menu.current) menu.current.open = false;};
  const focusMenu = () => requestAnimationFrame(() => menu.current?.querySelector('summary')?.focus());
  const menuReason = (action: 'active' | 'close' | 'stop' | 'text') => !controlling ? 'Take control to ' + ({active:'make this page active for the team.',close:'close a shared page.',stop:'shut down the shared browser.',text:'insert text into the page.'})[action]
    : busy ? 'Wait for the current browser action to finish.' : !state?.running ? 'No shared browser is running.'
    : action === 'active' && state.active_page === page ? 'This page is already active for the team.'
    : action === 'close' && (state.pages?.length ?? 0) <= 1 ? 'This is the last open page, so it cannot be closed. Shutting down the browser is a separate action.' : '';
  const input = (action: string, args: Record<string, unknown>) => {
    const current = stateRef.current;
    if (!current?.browser || !current.page) return;
    const target: InputTarget = {project: project || current.project, browser: current.browser, page: page || current.page,
      generation: current.generation, selection: selection.current};
    inputQueue.current = inputQueue.current.then(() => send(action, args, target));
  };
  const point = (event: {clientX: number; clientY: number}) => {
    const box = image.current!.getBoundingClientRect();
    const width = stateRef.current?.width || 1280, height = stateRef.current?.height || 800;
    return {x: Math.max(0, Math.min(width - 1, (event.clientX - box.left) * width / box.width)),
      y: Math.max(0, Math.min(height - 1, (event.clientY - box.top) * height / box.height))};
  };
  useEffect(() => {
    const page = image.current;
    if (!page || !controlling) return;
    const wheel = (event: WheelEvent) => {event.preventDefault(); input('scroll', {...point(event), delta: event.deltaY});};
    page.addEventListener('wheel', wheel, {passive: false});
    return () => page.removeEventListener('wheel', wheel);
  }, [frame, controlling]);
  const upload = async (file: File) => {
    if (!state) return;
    setBusy(true);
    try {
      const query = new URLSearchParams({project: project || state.project, ...state.browser ? {browser: state.browser} : {}, ...page ? {page} : {}});
      const response = await fetch('/api/runtime/browser/file?' + query, {
        method: 'POST', headers: {...sessionHeaders(), 'X-File-Name': encodeURIComponent(file.name)}, body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Upload failed');
      if (!alive.current) return;
      if ((project && result.project !== project) || (page && result.page !== page)) throw new Error('Upload response belongs to a different page.');
      setState(result); onState?.(result); setError(''); refreshFrame.current();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };

  return <div className="shared-browser">
    <div className="browser-controls">
    {!page && !!state?.instances?.length && <label className="browser-ownership">Watch browser
      <select aria-label="Agent browser" value={selectedBrowser || state.browser || ''} onChange={e => {selection.current++; stateRef.current = undefined; setSelectedBrowser(e.target.value); setState(undefined); setFrame(''); setError('');}}>
        {state.instances.map(instance => <option key={instance.id} value={instance.id}>{instance.label} · {instance.pages} {instance.pages === 1 ? 'page' : 'pages'}{instance.running ? '' : ' · stopped'}</option>)}
      </select>
    </label>}
    <form className="browser-toolbar" onSubmit={async e => {e.preventDefault(); if (!controlling || busy) return; const ok = await command('open', {url: address}); if (ok) {addressEditing.current = false; canonicalPending.current = true; refreshFrame.current();}}}>
      <button type="button" disabled={!controlling || !state?.running || busy} onClick={() => void command('reload')} aria-label="Reload shared page">↻</button>
      <input ref={addressInput} aria-label="Local preview URL" readOnly={!controlling || busy} title={controlling ? 'Press Enter to navigate the shared page' : 'Viewing only. Take control to navigate this shared page'} value={address} onFocus={() => {addressEditing.current = true;}} onBlur={() => {addressEditing.current = false;}} onChange={e => setAddress(e.target.value)} placeholder="http://localhost:5173" />
      <details className="browser-actions-menu" ref={menu} onKeyDown={e => {if (e.key === 'Escape') {e.preventDefault(); e.stopPropagation(); closeMenu(); focusMenu();}}}>
        <summary aria-label="Shared page actions" title="Shared page actions">⋯</summary>
        <div className="browser-menu-content">
          <button type="button" onClick={() => {setFit(value => !value); closeMenu(); focusMenu();}}>{fit ? 'Show actual frame size' : 'Fit to viewer'}</button><small>Changes this viewer only.</small>
          <button type="button" disabled={!!menuReason('text')} aria-describedby={confirmationId + '-text-reason'} onClick={() => {closeMenu(); textTool.current?.showModal();}}>Insert text into page</button>
          <small id={confirmationId + '-text-reason'}>{menuReason('text') || 'Inserts text into the currently focused page field.'}</small>
          {page && <><button type="button" disabled={!!menuReason('active')} aria-describedby={confirmationId + '-active-reason'} onClick={() => {closeMenu(); focusMenu(); void command('select_page');}}>Make active for the team</button>
            <small id={confirmationId + '-active-reason'}>{menuReason('active') || 'Changes the team’s active page, not just this viewer.'}</small>
            <hr /><button type="button" ref={closeButton} disabled={!!menuReason('close')} aria-describedby={confirmationId + '-reason'} onClick={() => {closeMenu(); confirm.current?.showModal();}}>Close shared page</button>
            <small id={confirmationId + '-reason'}>{menuReason('close') || 'Closes this exact page for everyone. Closing a viewer tab leaves it open.'}</small></>}
          <button type="button" disabled={!!menuReason('stop')} aria-describedby={confirmationId + '-stop-reason'} onClick={() => {closeMenu(); shutdown.current?.showModal();}}>Shut down shared browser</button>
          <small id={confirmationId + '-stop-reason'}>{menuReason('stop') || 'Closes every shared browser page for everyone.'}</small>
        </div>
      </details>
    </form>
    <div className="browser-ownership"><span role="status">{ownerLabel}{currentAction && <small> · {currentAction}</small>}</span>
      {page && state && <span className="browser-page-marker">{state.active_page === page ? 'Team active' : 'Viewing only'}</span>}
      <button type="button" ref={ownerButton} disabled={!state || busy} onClick={() => void command(controlling ? 'release' : 'claim')}>{controlling ? 'Hand back to agents' : 'Take control'}</button>
    </div>
    </div>
    {(error || closed) && <div role="alert" className="browser-error">{closed ? 'This shared browser page has closed or is unavailable. Its viewer cannot display another page.' : error}
      {closed && <div>{choosePage && <button ref={recoveryButton} onClick={choosePage}>Choose an open page</button>}{closeViewer && <button onClick={closeViewer}>Close viewer tab</button>}</div>}
    </div>}
    <div className={`browser-stage${fit ? '' : ' browser-actual-size'}`}>
      {state?.running && frame ? <img ref={image} src={frame} style={{aspectRatio: `${state.width}/${state.height}`, ...!fit ? {width: state.width} : {}}} draggable={false} tabIndex={controlling ? 0 : -1}
        alt="Live shared browser page" aria-label="Shared page. Click then type to interact; Tab navigates page fields. Escape exits the page."
        onPointerDown={e => {if (!controlling || e.button !== 0) return; e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); dragging.current = true; input('pointer', {...point(e), phase: 'mousePressed'});}}
        onPointerUp={e => {if (!dragging.current) return; dragging.current = false; input('pointer', {...point(e), phase: 'mouseReleased'});}}
        onPointerCancel={() => {dragging.current = false; input('pointer', {x: 0, y: 0, phase: 'mouseReleased'});}}
        onPointerMove={e => {if (controlling && dragging.current && Date.now() - lastMove.current > 60) {lastMove.current = Date.now(); input('pointer', {...point(e), phase: 'mouseMoved'});}}}
        onPaste={e => {if (controlling) {e.preventDefault(); input('type', {text: e.clipboardData.getData('text')});}}}
        onKeyDown={e => {
          if (e.key === 'Escape') {e.preventDefault(); e.stopPropagation(); if (addressInput.current && !addressInput.current.disabled) addressInput.current.focus(); else ownerButton.current?.focus(); return;}
          if (!controlling) return;
          if (e.ctrlKey && e.key.toLowerCase() === 'v') return;
          const keys = ['Enter','Tab','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];
          const modifiers = (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
          if (keys.includes(e.key) || (e.ctrlKey && e.key.toLowerCase() === 'a')) {e.preventDefault(); input('key', {key: e.key.toLowerCase() === 'a' ? 'a' : e.key, modifiers});}
          else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {e.preventDefault(); input('type', {text: e.key});}
        }} /> : !closed && <div className="browser-empty"><h2>Review a preview together</h2><p>Take control and open your local development URL. Agents can open the same page with room_browser.</p><p>A separate temporary profile keeps this session apart from your personal browser.</p></div>}
    </div>
    {state?.dialog && <div className="browser-dialog" role="alert"><p>{state.dialog.message}</p>
      {state.dialog.type === 'prompt' && <input aria-label="Page prompt answer" value={prompt} onChange={e => setPrompt(e.target.value)} />}
      <button disabled={!controlling || busy} onClick={() => void command('dialog', {accept: true, text: prompt})}>Accept</button>
      <button disabled={!controlling || busy} onClick={() => void command('dialog', {accept: false})}>Dismiss</button>
    </div>}
    {state?.file_chooser && <label className="browser-dialog">The shared page requested a file (up to 16 MB). <input aria-label="Choose file for shared page" type="file" disabled={!controlling || busy} onChange={e => {if(e.target.files?.[0]) void upload(e.target.files[0]);}} /></label>}
    <details className="browser-events"><summary>Page details &amp; activity</summary>
      <p className="browser-location">{state?.title || 'Shared page'} · {state?.url || 'No address'} · Project {project || state?.project} · Page {page || state?.page || 'current'}</p>
      <p className="browser-hint">{controlling ? 'Click the page to interact. Escape returns to the address. Hand back to agents when they should drive.' : 'Viewing only. Take control to interact; agents can keep observing.'}</p>
      {pageActivity?.activity && <p>Last observed: {pageActivity.activity.label} · {pageActivity.activity.action} · {pageActivity.activity.phase} · {new Date(pageActivity.activity.time * 1000).toLocaleTimeString()}</p>}
      <ul>{events.slice().reverse().map((event, index) => <li key={index}><time>{new Date(event.time * 1000).toLocaleTimeString()}</time> <strong>{event.kind}</strong> {event.text}</li>)}</ul>
    </details>
    <dialog className="browser-close-confirm" ref={confirm} aria-labelledby={confirmationId}
      onCancel={e => {if (busy) e.preventDefault();}}
      onClose={() => {if (closed) requestAnimationFrame(() => recoveryButton.current?.focus()); else focusMenu();}}>
      <h2 id={confirmationId}>Close shared browser page?</h2><p>{state?.title} · {state?.url} · Page {page}</p>
      <p>This closes the actual page for everyone and may interrupt work using it. Other pages and file tabs stay open.</p>
      {error && <p role="alert">{error}</p>}
      <button autoFocus disabled={busy} onClick={() => confirm.current?.close()}>Keep page</button>
      <button disabled={!controlling || busy} onClick={async () => {setBusy(true); const ok = await send('close_page'); setBusy(false); if (ok) confirm.current?.close();}}>Close shared page</button>
    </dialog>
    <dialog className="browser-close-confirm" ref={shutdown} aria-labelledby={confirmationId + '-shutdown'} onCancel={e => {if (busy) e.preventDefault();}} onClose={focusMenu}>
      <h2 id={confirmationId + '-shutdown'}>Shut down this browser?</h2><p>All pages in {state?.browser_label || 'this browser'} close for everyone viewing them. Other agents' browsers and file, review and Changes viewers stay open.</p>
      {error && <p role="alert">{error}</p>}<button autoFocus disabled={busy} onClick={() => shutdown.current?.close()}>Keep browser</button>
      <button disabled={!controlling || busy} onClick={async () => {if (await command('stop')) shutdown.current?.close();}}>Shut down shared browser</button>
    </dialog>
    <dialog className="browser-close-confirm" ref={textTool} aria-labelledby={confirmationId + '-insert'} onClose={focusMenu}>
      <h2 id={confirmationId + '-insert'}>Insert text into page</h2><p>Types into the currently focused field on {state?.title || 'this shared page'} · Page {page || state?.page}.</p>
      <form className="browser-type" onSubmit={async e => {e.preventDefault(); if (await command('type', {text})) {setText(''); textTool.current?.close();}}}>
        <input autoFocus aria-label="Text to insert into the focused page field" value={text} onChange={e => setText(e.target.value)} />
        <button disabled={!controlling || busy || !text}>Insert text</button><button type="button" onClick={() => textTool.current?.close()}>Cancel</button>
      </form>{error && <p role="alert">{error}</p>}
    </dialog>
  </div>;
}
