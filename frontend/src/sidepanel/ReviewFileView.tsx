import { useEffect, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import type { Review } from '../lib/types';
import type { PanelRequest } from './usePanelTabs';

type Artifact = {
  kind: 'ok' | 'binary' | 'unavailable'; review: Review; task: {id: string; title: string}; path: string;
  sha256?: string; bytes?: number; text?: string; truncated?: boolean; changed_since?: boolean; reason?: string;
};
export default function ReviewFileView({project, request, active, review}: {project: string; request: PanelRequest; active: boolean; review?: Review}) {
  const [data, setData] = useState<Artifact | null>(null), [error, setError] = useState('');
  const [reload, setReload] = useState(0), [loading, setLoading] = useState(false);
  const id = String(request.review_id ?? ''), path = String(request.path ?? '');
  useEffect(() => {
    if (!active) return;
    let current = true;
    setLoading(true); setError('');
    const query = new URLSearchParams({project, review_id: id, path});
    runtime<Artifact>('/review-artifact?' + query).then(d => {if (current) setData(d);})
      .catch(e => {if (current) {setData(null); setError(errorMessage(e));}}).finally(() => {if (current) setLoading(false);});
    return () => {current = false;};
  }, [project, id, path, active, reload]);
  const record = review ?? data?.review;
  return <section className="panel-document">
    <header><div><p className="panel-eyebrow">CAPTURED FILE REVIEW</p><h2>{path}</h2><p>Review {id}{record && ` · ${record.revision}`}</p></div><button disabled={loading} onClick={() => setReload(n => n + 1)}>{loading ? 'Loading...' : 'Refresh'}</button></header>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {loading && !data && <p role="status">Opening captured source...</p>}
    {data && <>
      <p>Task: {data.task.title} · {data.task.id}</p>
      {data.sha256 && <p className="panel-hash">SHA-256 {data.sha256} · {data.bytes} bytes</p>}
      {data.changed_since && <p className="panel-warning">Working copy has changed since review. These are the captured bytes.</p>}
      {record && <div className="panel-verdict"><strong>{record.reviewer} · {record.status.replace(/_/g, ' ')}</strong>{record.findings && <p>{record.findings}</p>}<p>Recorded review of {record.revision}. Viewing does not submit a verdict.</p></div>}
      {data.kind === 'ok' && <><pre tabIndex={0} aria-label={`Captured contents of ${path}`}>{data.text}</pre>{data.truncated && <p className="panel-warning">Only the first 1,000,000 characters are shown.</p>}</>}
      {data.kind === 'binary' && <p>Captured binary file. Text preview is unavailable.</p>}
      {data.kind === 'unavailable' && <p className="panel-error" role="alert">{data.reason}. The working copy is not substituted.</p>}
    </>}
  </section>;
}
