import { useEffect, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';

export default function ChangesView({active, agent}: {active: boolean; agent?: string}) {
  const [data, setData] = useState<Record<string,string> | null>(null), [error, setError] = useState('');
  const [reload, setReload] = useState(0), [loading, setLoading] = useState(false), [received, setReceived] = useState('');
  useEffect(() => {
    if (!active) return;
    let current = true;
    setLoading(true); setError('');
    runtime<Record<string,string>>('/diffs').then(d => {if (current) {setData(d); setReceived(new Date().toLocaleTimeString());}})
      .catch(e => {if (current) setError(errorMessage(e));}).finally(() => {if (current) setLoading(false);});
    return () => {current = false;};
  }, [active, reload]);
  const entries = Object.entries(data ?? {}).filter(([name]) => !agent || name === agent);
  return <section className="panel-document">
    <header><div><h2>Changes{agent ? ` · ${agent}` : ''}</h2><p>Current worktree diffs{received ? ` · Received ${received}` : ''}</p></div><button disabled={loading} onClick={() => setReload(n => n + 1)}>{loading ? 'Loading...' : 'Refresh'}</button></header>
    {error && <div className="panel-error" role="alert">{error}{data && <p>Previously received diffs remain below.</p>}</div>}
    {!data && !error && <p>Loading worktree changes...</p>}
    {data && !entries.length && <p>No worktree diffs are available.</p>}
    {entries.map(([name,diff]) => <article key={name}><h3>{name}</h3><pre tabIndex={0} aria-label={`${name} worktree diff`}>{diff || 'No changes'}</pre></article>)}
  </section>;
}
