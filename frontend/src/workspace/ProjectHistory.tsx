/* Saved project history has its own state. Never mount the active room's composer here. */
import { useEffect, useState } from 'react';
import { api, errorMessage, runtime } from '../lib/api';
import type { ProjectDetail, ProjectSummary } from './projectData';

interface SavedMessage {
  id: number; sender: string; text: string; timestamp?: number; time?: string;
  channel: string; reply_to?: number; attachments?: { name: string; url: string }[];
}
function uploadUrl(value: string): string | undefined {
  try { const url = new URL(value, location.href); return url.origin === location.origin && /^\/uploads\/[^/]+$/.test(url.pathname) ? url.href : undefined; }
  catch { return undefined; }
}

export default function ProjectHistory({ project, session, summary, runningName, onReturn, onDetails }: {
  project: string; session: string; runningName: string; onReturn: () => void; onDetails: () => void;
  summary?: ProjectSummary;
}) {
  const [data, setData] = useState<{ detail: ProjectDetail; messages: SavedMessage[] } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [count, setCount] = useState(200);
  const [refresh, setRefresh] = useState(0);
  const [received, setReceived] = useState('');
  useEffect(() => {
    let current = true;
    setLoading(true); setError('');
    void (async () => {
      try {
        const detail = await runtime<ProjectDetail>(`/projects/${encodeURIComponent(project)}`);
        if (!detail.sessions.some(s => s.id === session)) throw new Error('This chat is no longer listed in the selected project. Open its project details to choose a chat.');
        const messages = await api<SavedMessage[]>(`/api/messages?${new URLSearchParams({ channel: session, limit: String(count) })}`);
        if (!current) return;
        if (messages.some(m => m.channel !== session)) throw new Error('The history response belongs to another chat.');
        setData({ detail, messages }); setReceived(new Date().toLocaleTimeString());
      } catch (e) { if (current) setError(errorMessage(e)); }
      finally { if (current) setLoading(false); }
    })();
    return () => { current = false; };
  }, [project, session, count, refresh]);
  const title = data?.detail.sessions.find(s => s.id === session)?.title ?? 'Chat history';
  return <div className="bd-root pn-history">
    <header className="workspace-page-heading"><div><span className="workspace-eyebrow">SAVED HISTORY · READ ONLY</span>
      <h1>{title}</h1><p>{summary?.name ?? data?.detail.name ?? 'Loading project…'}{(summary?.archived ?? data?.detail.archived) ? ' · Archived project' : ''}</p></div>
      <button className="bd-btn" onClick={onDetails}>Project details</button></header>
    <div className="pn-running"><span>Running room: <strong>{runningName || 'No project attached'}</strong>. Its chat and draft stay open.</span>
      <button className="bd-btn" onClick={onReturn}>Return to running chat</button></div>
    <div className="pn-history-actions"><span>This view has no message or project actions.</span>
      <button className="bd-btn" disabled={loading} onClick={() => setRefresh(v => v + 1)}>Refresh history</button>
      {received && <small>Last read {received}</small>}</div>
    {error && <p className="bd-error" role="alert">{error}</p>}
    {loading && <p role="status">Loading saved history…</p>}
    {data && <>
      {data.messages.length >= count && <button className="bd-btn" disabled={loading} onClick={() => setCount(v => v + 200)}>Load older messages</button>}
      {!data.messages.length && <p>No saved messages in this chat.</p>}
      <ol className="pn-messages">{data.messages.map(m => <li key={m.id}>
        <article className="pn-message"><header><strong>{m.sender === 'user' ? window.username || 'You' : data.detail.team.find(a => a.id === m.sender)?.name || m.sender}</strong>
          <time>{m.timestamp ? new Date(m.timestamp * 1000).toLocaleString() : m.time}</time><small>#{m.id}</small></header>
          {m.reply_to !== undefined && <small>Reply to #{m.reply_to}</small>}
          <div className="pn-message-text">{m.text}</div>
          {m.attachments?.map((a, i) => { const url = uploadUrl(a.url); return url ? <a key={i} href={url} target="_blank" rel="noopener noreferrer">{a.name || 'Attachment'} (opens in new tab)</a> : <span key={i}>Attachment unavailable</span>; })}
        </article>
      </li>)}</ol>
    </>}
  </div>;
}
