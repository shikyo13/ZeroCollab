/* File tab of the workspace side panel: a read-only view of a file referenced in chat, with the copy of
 * the code and Git state it came from. Opened by the legacy chat's 'workspace-side-panel' event
 * (static/chat/format.js); the server resolves every reference again (file_preview.py). */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import './fileview.css';

type Lines = { start: number | null; end: number | null };
type Resolved = Lines & { path: string; root_label?: string; root_index?: number | null };
type View = Resolved & {
  kind: 'ok';
  absolute: string;
  branch?: string | null;
  head?: string | null;
  state: string;
  source: 'working copy' | 'revision';
  at?: string;
  binary: boolean;
  truncated?: boolean;
  text: string;
  changed_since_message?: boolean;
  committed_before_message?: string | null;
};
type Ambiguous = Resolved & { kind: 'ambiguous'; candidates: string[]; more: number };
type Missing = Resolved & { kind: 'missing'; searched: string[]; directory?: boolean };
type Outside = Resolved & { kind: 'outside' };
type Failed = { kind: 'error'; error: string };
type Result = View | Ambiguous | Missing | Outside | Failed;

type Props = { request: Record<string, unknown> | null; active: boolean; onClose: () => void };

const LINE_HEIGHT = 20;  // px; fileview.css uses the same value so the highlight band lines up

function str(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function lineSuffix({ start, end }: Lines): string {
  if (!start) return '';
  return end && end !== start ? `:${start}-${end}` : `:${start}`;
}

function messageTime(seconds: unknown): string {
  const value = Number(seconds);
  return value ? new Date(value * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
}

export default function FileView({ request, active }: Props) {
  // A choice made inside the panel (one of several matches, or an earlier revision) overrides the request.
  const [choice, setChoice] = useState<{ ref?: string; root?: number | null; at?: string }>({});
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const body = useRef<HTMLDivElement>(null);
  const ref = choice.ref ?? str(request?.ref);
  const sender = str(request?.sender);
  const time = request?.time;
  const root = choice.root ?? (request?.root as number | null | undefined) ?? null;

  useEffect(() => { setChoice({}); setNotice(''); }, [request]);

  useEffect(() => {
    if (!ref || !active) return;
    let current = true;
    const query = new URLSearchParams({ ref, sender });
    if (time) query.set('message_time', str(time));
    if (choice.at) query.set('at', choice.at);
    if (root != null) query.set('root', String(root));
    setLoading(true);
    api<Result>('/api/file-view?' + query)
      .then(data => { if (current) setResult(data); })
      .catch(error => { if (current) setResult({ kind: 'error', error: errorMessage(error) }); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [ref, sender, time, root, choice.at, active]);

  // Bring the referenced lines into view, a third of the way down.
  useLayoutEffect(() => {
    const el = body.current;
    if (!el || result?.kind !== 'ok') return;
    el.scrollTop = result.start ? Math.max(0, (result.start - 1) * LINE_HEIGHT - el.clientHeight / 3) : 0;
  }, [result]);

  if (!request) return <div className="fileview-empty">Click a file name in a chat message to open it here.</div>;
  if (!result || (loading && result.kind !== 'ok')) return <div className="fileview-empty">Opening {ref}...</div>;

  async function open(target: 'editor' | 'explorer') {
    setNotice('');
    try {
      const done = await api<{ opened_with: string }>('/api/file-view/open', { ref, sender, root, target });
      setNotice(`Opened in ${done.opened_with}`);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function reveal(path: string) {
    setNotice('');
    try {
      await api('/api/open-path', { path });
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setNotice('Path copied');
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  if (result.kind === 'error') {
    return <div className="fileview-message" role="alert"><strong>{ref}</strong><p>{result.error}</p></div>;
  }
  if (result.kind === 'ambiguous') {
    return <div className="fileview-message">
      <strong>Several files are named {result.path}</strong>
      <p>In the {result.root_label}. Choose one:</p>
      <ul className="fileview-candidates">
        {result.candidates.map(path => <li key={path}>
          <button onClick={() => setChoice({ ref: path + lineSuffix(result), root: result.root_index })}>{path}</button>
        </li>)}
      </ul>
      {result.more > 0 && <p>and {result.more} more; use the full path in chat to pick one of those.</p>}
    </div>;
  }
  if (result.kind === 'missing' || result.kind === 'outside') {
    const absolute = /^([A-Za-z]:\/|\/)/.test(result.path);
    return <div className="fileview-message">
      <strong>{result.path}</strong>
      {result.kind === 'outside'
        ? <p>This file is outside the project's repositories and agent worktrees, so it is not shown here.</p>
        : result.directory
          ? <p>This is a folder.</p>
          : <p>Not found in: {result.searched.join(', ') || 'no repositories are attached'}.</p>}
      {absolute && (result.kind === 'outside' || result.directory) && <div className="fileview-actions">
        <button onClick={() => reveal(result.path)}>Show in Explorer</button>
        <button onClick={() => copy(result.path)}>Copy path</button>
      </div>}
      {notice && <p className="fileview-notice" role="status">{notice}</p>}
    </div>;
  }

  const lines = result.text.split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  const start = result.start && result.start <= lines.length ? result.start : null;
  const end = start ? Math.min(result.end ?? start, lines.length) : null;
  const linkedFrom = [sender && `linked from ${sender}`, messageTime(time)].filter(Boolean).join(', ');

  return <div className="fileview">
    <header className="fileview-head">
      <h2><span>{result.path}</span>{start && <small>{lineSuffix({ start, end })}</small>}</h2>
      <p className="fileview-meta">
        <span>{result.root_label}</span>
        {result.branch && <span>{result.branch}{result.head && ` @ ${result.head}`}</span>}
        <span className={`fileview-badge fileview-${result.state.replace(/\s+/g, '-')}`}>{result.source === 'revision' ? `commit ${result.at}` : result.state}</span>
        {linkedFrom && <span>{linkedFrom}</span>}
      </p>
    </header>
    {result.source === 'revision'
      ? <div className="fileview-banner">
          Showing commit {result.at}: the last commit before the message, inferred from commit dates.
          <button onClick={() => setChoice(c => ({ ...c, at: undefined }))}>Show current version</button>
        </div>
      : result.changed_since_message && <div className="fileview-banner">
          This file changed after the message.
          {result.committed_before_message && <button onClick={() => setChoice(c => ({ ...c, at: result.committed_before_message ?? undefined }))}>
            Show last commit before it (inferred)
          </button>}
        </div>}
    <div className="fileview-actions">
      <button onClick={() => open('editor')}>Open in VS Code</button>
      <button onClick={() => open('explorer')}>Show in Explorer</button>
      <button onClick={() => copy(result.absolute)}>Copy path</button>
      {notice && <span className="fileview-notice" role="status">{notice}</span>}
    </div>
    {result.binary
      ? <div className="fileview-message">Binary file, not shown. Open it in an editor instead.</div>
      : <div className="fileview-body" ref={body} tabIndex={0} aria-label={`Contents of ${result.path}`}>
          <div className="fileview-code" style={{ height: lines.length * LINE_HEIGHT }}>
            {start && <div className="fileview-hit" style={{ top: (start - 1) * LINE_HEIGHT, height: ((end ?? start) - start + 1) * LINE_HEIGHT }} />}
            <pre className="fileview-gutter" aria-hidden="true">{lines.map((_, i) => i + 1).join('\n')}</pre>
            <pre className="fileview-text">{lines.join('\n')}</pre>
          </div>
          {result.truncated && <p className="fileview-notice">Only the first 1 MB is shown. Open it in VS Code for the rest.</p>}
        </div>}
  </div>;
}
