import { useCallback, useEffect, useRef, useState } from 'react';
import { runtime, sessionToken, errorMessage } from '../lib/api';
import './browser.css';

type BrowserState = {
  running: boolean; project: string; url: string; title: string; owner: string; owner_label: string;
  width: number; height: number; lease_seconds: number | null;
  events: {time: number; kind: string; text: string}[];
  dialog: {type: string; message: string} | null; file_chooser: boolean;
};

export default function Browser({ active }: {active: boolean}) {
  const [state, setState] = useState<BrowserState>();
  const [address, setAddress] = useState('http://localhost:5173');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [frame, setFrame] = useState('');
  const [text, setText] = useState('');
  const [prompt, setPrompt] = useState('');
  const image = useRef<HTMLImageElement>(null);
  const stateRef = useRef(state);
  const inputQueue = useRef<Promise<unknown>>(Promise.resolve());
  const dragging = useRef(false);
  const lastMove = useRef(0);
  const addressEditing = useRef(false);
  stateRef.current = state;
  const controlling = state?.owner === 'user';
  useEffect(() => {if (state?.url && !addressEditing.current) setAddress(state.url);}, [state?.url]);

  useEffect(() => {
    if (!active) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>, objectUrl = '';
    const abort = new AbortController();
    async function poll() {
      try {
        const next = await runtime<BrowserState>('/browser');
        if (stopped) return;
        setState(next);
        if (next.running) {
          const response = await fetch('/api/runtime/browser/frame?project=' + encodeURIComponent(next.project), {headers: {'X-Session-Token': sessionToken()}, signal: abort.signal});
          if (response.ok) {
            const blob = await response.blob();
            if (stopped) return;
            const fresh = URL.createObjectURL(blob);
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = fresh; setFrame(fresh);
          }
        } else { setFrame(''); }
      } catch (e) { if (!stopped) setError(errorMessage(e)); }
      finally { if (!stopped) timer = setTimeout(poll, 650); }
    }
    void poll();
    return () => { stopped = true; abort.abort(); clearTimeout(timer); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [active]);

  const send = useCallback(async (action: string, args: Record<string, unknown> = {}) => {
    const current = stateRef.current;
    if (!current) return;
    try {
      const next = await runtime<BrowserState>('/browser', {action, project: current.project, ...args});
      setState(next); setError('');
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  const command = async (action: string, args: Record<string, unknown> = {}) => {
    setBusy(true); await send(action, args); setBusy(false);
  };
  const input = (action: string, args: Record<string, unknown>) => {
    inputQueue.current = inputQueue.current.then(() => send(action, args));
  };
  const point = (event: {clientX: number; clientY: number}) => {
    const box = image.current!.getBoundingClientRect();
    const width = stateRef.current?.width || 1280, height = stateRef.current?.height || 800;
    return {x: Math.max(0, Math.min(width - 1, (event.clientX - box.left) * width / box.width)),
      y: Math.max(0, Math.min(height - 1, (event.clientY - box.top) * height / box.height))};
  };
  const upload = async (file: File) => {
    if (!state) return;
    setBusy(true);
    try {
      const response = await fetch('/api/runtime/browser/file?project=' + encodeURIComponent(state.project), {
        method: 'POST', headers: {'X-Session-Token': sessionToken(), 'X-File-Name': encodeURIComponent(file.name)}, body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Upload failed');
      setState(result); setError('');
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };

  return <div className="shared-browser">
    <div className="browser-heading"><div><span className="workspace-eyebrow">SHARED WORKSPACE</span><h1>Browser</h1>
      <p>One local preview, visible to you and the team.</p></div>
      <span className="browser-owner" role="status">{controlling ? 'You are controlling' : state?.owner ? `${state.owner_label} is controlling` : 'Available to control'}</span>
    </div>
    <form className="browser-toolbar" onSubmit={e => {e.preventDefault(); void command('open', {url: address});}}>
      <button type="button" disabled={!controlling || !state?.running || busy} onClick={() => void command('reload')} aria-label="Reload shared page">↻</button>
      <input aria-label="Local preview URL" value={address} onFocus={() => {addressEditing.current = true;}} onBlur={() => {addressEditing.current = false;}} onChange={e => setAddress(e.target.value)} placeholder="http://localhost:5173" />
      <button disabled={!controlling || busy}>Open</button>
      <button type="button" className="browser-primary" disabled={!state || busy} onClick={() => void command(controlling ? 'release' : 'claim')}>{controlling ? 'Release control' : 'Take control'}</button>
      <button type="button" disabled={!controlling || !state?.running || busy} onClick={() => void command('stop')}>Stop browser</button>
      <button type="button" disabled={!controlling || !state?.running || busy} onClick={() => void command('resize', {width: Math.round(image.current?.clientWidth || 720), height: 600})}>Fit page</button>
    </form>
    {error && <p role="alert" className="browser-error">{error}</p>}
    {state?.running && <p className="browser-location">{state.title || 'Shared page'} · {state.url}</p>}
    <div className="browser-stage">
      {state?.running && frame ? <img ref={image} src={frame} style={{aspectRatio: `${state.width}/${state.height}`}} draggable={false} tabIndex={controlling ? 0 : -1}
        alt="Live shared browser page" aria-label="Shared page. Click then type to interact; Tab navigates page fields. Escape exits the page."
        onPointerDown={e => {if (!controlling) return; e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); dragging.current = true; input('pointer', {...point(e), phase: 'mousePressed'});}}
        onPointerUp={e => {if (!dragging.current) return; dragging.current = false; input('pointer', {...point(e), phase: 'mouseReleased'});}}
        onPointerCancel={() => {dragging.current = false; input('pointer', {x: 0, y: 0, phase: 'mouseReleased'});}}
        onPointerMove={e => {if (controlling && dragging.current && Date.now() - lastMove.current > 60) {lastMove.current = Date.now(); input('pointer', {...point(e), phase: 'mouseMoved'});}}}
        onWheel={e => {if (controlling) input('scroll', {...point(e), delta: e.deltaY});}}
        onPaste={e => {if (controlling) {e.preventDefault(); input('type', {text: e.clipboardData.getData('text')});}}}
        onKeyDown={e => {
          if (!controlling) return;
          if (e.key === 'Escape') {e.currentTarget.blur(); return;}
          if (e.ctrlKey && e.key.toLowerCase() === 'v') return;
          const keys = ['Enter','Tab','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];
          const modifiers = (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
          if (keys.includes(e.key) || (e.ctrlKey && e.key.toLowerCase() === 'a')) {e.preventDefault(); input('key', {key: e.key.toLowerCase() === 'a' ? 'a' : e.key, modifiers});}
          else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {e.preventDefault(); input('type', {text: e.key});}
        }} /> : <div className="browser-empty"><h2>Review a preview together</h2><p>Take control and open your local development URL. Agents can open the same page with room_browser.</p><p>A separate temporary profile keeps this session apart from your personal browser.</p></div>}
    </div>
    <p className="browser-hint">{controlling ? 'Click the page to interact. Escape returns keyboard focus to the room. Release control when agents should drive.' : 'Viewing only. Take control to interact; agents can keep observing.'}</p>
    {controlling && state?.running && <form className="browser-type" onSubmit={e => {e.preventDefault(); void command('type', {text}); setText('');}}>
      <input aria-label="Text to insert into the focused page field" placeholder="Paste or type text for the focused page field" value={text} onChange={e => setText(e.target.value)} />
      <button disabled={busy || !text}>Insert text</button>
    </form>}
    {state?.dialog && <div className="browser-dialog" role="alert"><p>{state.dialog.message}</p>
      {state.dialog.type === 'prompt' && <input aria-label="Page prompt answer" value={prompt} onChange={e => setPrompt(e.target.value)} />}
      <button disabled={!controlling || busy} onClick={() => void command('dialog', {accept: true, text: prompt})}>Accept</button>
      <button disabled={!controlling || busy} onClick={() => void command('dialog', {accept: false})}>Dismiss</button>
    </div>}
    {state?.file_chooser && <label className="browser-dialog">The shared page requested a file (up to 16 MB). <input aria-label="Choose file for shared page" type="file" disabled={!controlling || busy} onChange={e => {if(e.target.files?.[0]) void upload(e.target.files[0]);}} /></label>}
    <details className="browser-events"><summary>Page activity · {state?.events.length ?? 0}</summary>
      <ul>{state?.events.slice().reverse().map((event, index) => <li key={index}><time>{new Date(event.time * 1000).toLocaleTimeString()}</time> <strong>{event.kind}</strong> {event.text}</li>)}</ul>
    </details>
  </div>;
}
