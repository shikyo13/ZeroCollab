import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import type { Agents } from '../board/shared';
import type { RunInfo } from '../lib/types';
import { openDecision, type Decision, type Draft, type Listing } from './model';
import Setup from './Setup';
import DecisionDetail from './DecisionDetail';
import ChatDecision from './ChatDecision';
import './decisions.css';

export default function Decisions({project, active, channel, agents, runs, onTask}: {
  project: string; active: boolean; channel: string; agents: Agents; runs: RunInfo[]; onTask: (id: string) => void;
}) {
  const [listing, setListing] = useState<Listing | null>(null), [selected, setSelected] = useState(() => new URLSearchParams(location.search).get('decision') || '');
  const [data, setData] = useState<Decision | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0), [announcement, setAnnouncement] = useState('');
  const alive = useRef(true), creating = useRef(false), versions = useRef(new Map<string, number>()), selectedRef = useRef(selected); selectedRef.current = selected;
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const receive = useCallback((d: Decision) => {
    if (!alive.current || d.updated < (versions.current.get(d.id) ?? 0)) return;
    versions.current.set(d.id, d.updated);
    setListing(list => ({...(list || {available:[]}), decisions: [d, ...(list?.decisions || []).filter(x => x.id !== d.id)]}));
    if (selectedRef.current === d.id) setData(d);
  }, []);
  useRoomEvent<Decision>('decision', e => receive(e.data));
  useEffect(() => {
    const sync = () => setSelected(new URLSearchParams(location.search).get('decision') || '');
    window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync);
  }, []);
  useEffect(() => {
    let current = true, timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {const next = await api<Listing>('/api/decisions'); if (current) setListing(previous => ({...next, decisions: next.decisions.map(d => {
        const known = previous?.decisions.find(p => p.id === d.id);
        return known && known.updated > d.updated ? known : d;
      })}));}
      catch (e) {if (current) setError(errorMessage(e));}
      finally {if (current && active) timer = setTimeout(load, 4000);}
    }
    void load(); return () => {current = false; clearTimeout(timer);};
  }, [project, active, channel, refresh]);
  useEffect(() => {
    setData(null); if (!selected) return;
    let current = true;
    api<Decision>('/api/decisions/' + encodeURIComponent(selected)).then(d => {if (current && d.id === selected) receive(d);})
      .catch(e => {if (current) setError(errorMessage(e));});
    return () => {current = false;};
  }, [selected, refresh, receive]);
  const matching = listing?.decisions.filter(d => d.channel && d.channel === channel).sort((a,b)=>b.updated-a.updated) || [];
  const transcript = matching.find(d=>!['recorded','stopped'].includes(d.stage));
  const select = (id: string) => {
    if (id) openDecision(id);
    else {const url = new URL(location.href); url.searchParams.delete('decision'); history.pushState({},'',url.pathname + url.search); window.dispatchEvent(new PopStateEvent('popstate'));}
    setError('');
  };
  const create = async (draft: Draft) => {
    if (creating.current) return false; creating.current = true; setBusy(true); setError('');
    try {const d = await api<Decision>('/api/decisions',{question: draft.question.trim(), criteria: draft.criteria.split('\n').map(s => s.trim()).filter(Boolean), participants: draft.participants, lead: draft.lead});
      if (!alive.current) return false; versions.current.set(d.id,d.updated); setListing(l => l ? {...l,decisions:[d,...l.decisions]} : l); select(d.id); setData(d); setAnnouncement('Draft saved. No participant work has started.'); return true;
    } catch (e) {if (alive.current) setError(errorMessage(e)); return false;} finally {creating.current = false; if (alive.current) setBusy(false);}
  };
  return <><section className="decision-root" aria-label="Team decisions">
    <header className="decision-heading"><div><p className="decision-eyebrow">TEAM DECISIONS</p>{!selected&&<h2>Decisions</h2>}</div><div className="decision-actions">
      {selected && <button className="bd-btn" onClick={() => select('')}>Back to discussions</button>}
      <button className="bd-btn" onClick={() => {setError(''); setRefresh(n => n + 1);}}>Refresh decisions</button></div></header>
    <p className="decision-sr-only" role="status">{announcement}</p>
    {error && <div className="decision-error" role="alert">{error}<button className="bd-btn" onClick={() => {setError('');setRefresh(n=>n+1);}}>Retry loading</button></div>}
    {selected ? data ? <DecisionDetail key={data.id} d={data} listing={listing} active={active} agents={agents} runs={runs} receive={receive} onTask={onTask} /> : !error && <p role="status">Loading discussion...</p>
      : <div className="decision-layout"><Setup project={project} listing={listing} agents={agents} busy={busy} save={create} /><section className="decision-card"><h2>Saved discussions</h2>
        {!listing && !error && <p>Loading discussions...</p>}{listing?.decisions.length === 0 && <p>No saved discussions yet.</p>}
        {listing?.decisions.map(d => <button className="decision-list-item" key={d.id} onClick={() => select(d.id)}><strong>{d.question}</strong><small>{d.stage.replace(/_/g,' ')} · {d.stage === 'recorded' ? 'Recorded' : d.outcome} · {d.participants.join(', ')}</small></button>)}
      </section></div>}
  </section>{transcript&&<ChatDecision key={`${project}:${transcript.id}`} project={project} summary={transcript}/>}</>;
}
