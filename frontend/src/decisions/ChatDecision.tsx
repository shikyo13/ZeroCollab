import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import { legacy } from '../legacy';
import { openDecision, type Decision, type Summary } from './model';
import './decisions.css';

const labels: Record<string,string> = {draft:'Draft',positions:'Collecting proposals',critique:'Comparing proposals',proposal:'Preparing a recommendation',verdicts:'Gathering verdicts',review:'Ready for your decision',recorded:'Recorded',stopped:'Stopped'};

function useDecision(id: string, project: string, active = true) {
  const [data,setData]=useState<Decision|null>(null),[error,setError]=useState('');
  const current=useRef<Decision|null>(null);
  const receive=(d: Decision)=>{if(d.id===id && (!current.current || d.updated>=current.current.updated)){current.current=d;setData(d);setError('');}};
  useRoomEvent<Decision>('decision',e=>receive(e.data));
  useEffect(()=>{
    let alive=true,timer:ReturnType<typeof setTimeout>;
    current.current=null;setData(null);setError('');
    async function load(){try{const next=await api<Decision>('/api/decisions/'+encodeURIComponent(id));if(alive)receive(next);}catch(e){if(alive)setError(errorMessage(e));}finally{if(alive&&active)timer=setTimeout(load,4000);}}
    if(id)void load();return()=>{alive=false;clearTimeout(timer);};
  },[id,project,active]);
  return {data:data?.id===id?data:null,error};
}

function Progress({d}:{d:Decision}) {
  const count=d.coverage;
  return <><p className="decision-muted">{labels[d.stage] || d.stage} · Round {d.round ?? 1}</p>
    {count && <p role="status">{count.submitted.length} of {count.invited.length} initial proposals submitted{count.missing.length ? ` · Waiting for ${count.missing.join(', ')}` : ''}</p>}
    {d.owed.length>0 && <p className="decision-muted">This stage is waiting for {d.owed.join(', ')}</p>}
    {d.interrupted && <p className="decision-warning">{d.interrupted}</p>}
    {(d.phase==='review_ready'||d.phase==='recorded') && <p>Outcome: {d.outcome}</p>}
  </>;
}

export default function ChatDecision({summary,project}:{summary:Summary;project:string}) {
  const {data,error}=useDecision(summary.id,project);
  const [host]=useState(()=>document.createElement('section'));
  useEffect(()=>{host.className='chat-decision-host';host.setAttribute('aria-label','Current team decision');legacy.header.after(host);return()=>host.remove();},[host]);
  return createPortal(<article className="chat-decision-card">
    <div><span className="decision-eyebrow">TEAM DECISION</span><strong>{data?.question || summary.question}</strong>
      {data?<Progress d={data}/>:<p role="status">{labels[summary.stage]} · Loading participation…</p>}
      {error&&<p className="decision-error" role="alert">Could not refresh decision: {error}</p>}
    </div><button className="bd-btn" onClick={e=>window.dispatchEvent(new CustomEvent('workspace-side-panel',{detail:{tab:'decision',decision_id:summary.id,opener:e.currentTarget}}))}>Open details</button>
  </article>,host);
}

export function DecisionPanel({id,project,active,close}:{id:string;project:string;active:boolean;close:()=>void}) {
  const {data:d,error}=useDecision(id,project,active),heading=useRef<HTMLHeadingElement>(null),focused=useRef(false);
  useEffect(()=>{if(active&&d&&!focused.current){heading.current?.focus();focused.current=true;}if(!active)focused.current=false;},[active,d]);
  if(!d)return <section className="decision-dock"><button className="bd-btn" onClick={close}>Close details</button><p role={error?'alert':'status'}>{error || 'Loading decision…'}</p></section>;
  const current=d.contributions.filter(c=>(c.round??1)===(d.round??1));
  return <section className="decision-dock" aria-label="Decision details">
    <button className="bd-btn" onClick={close}>Close details</button>
    <h2 ref={heading} tabIndex={-1}>{d.question}</h2><Progress d={d}/>
    {error&&<p className="decision-error" role="alert">Could not refresh: {error}</p>}
    {!!d.inputs?.length && <section><h3>Your input</h3>{d.inputs.map((input,i)=><blockquote key={i}><strong>{input.kind==='requirement'?'Requirement':'Suggestion'}</strong><p>{input.quote}</p></blockquote>)}</section>}
    <section><h3>Current round</h3>{current.length===0&&<p>No submissions yet.</p>}{current.map(c=><article className="decision-contribution" key={c.id}>
      <h4>{c.author} · {c.kind}</h4>{c.sealed ? <p>Proposal sealed until this round is revealed.</p> : <>
        <p className="decision-text">{c.text || c.reason || 'Status recorded'}</p>
        {!!c.evidence?.length&&<ul>{c.evidence.map((text,i)=><li key={i}>{text}</li>)}</ul>}
        {c.attachments?.filter(a=>/^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(a.url)).map(a=><a key={a.url} href={a.url} target="_blank" rel="noreferrer">{a.name || 'View attachment'}</a>)}
      </>}
    </article>)}</section>
    {!!d.rounds?.length&&<details><summary>Earlier rounds</summary>{d.rounds.map(round=><div key={round.round}><h4>Round {round.round}</h4><p>{round.revealed?'Proposals revealed':'Proposals remain sealed'} · {round.concluded?`Recommendation: ${round.outcome}`:'No recommendation recorded'}</p><p>{round.reason}</p>
      {round.revealed&&d.contributions.filter(c=>(c.round??1)===round.round&&!c.sealed).map(c=><p className="decision-text" key={c.id}>{c.author}: {c.text || c.reason}</p>)}
    </div>)}</details>}
    <p className="decision-muted">Discussion {d.id}. Continue discussing in the ordinary chat.</p>
    <button className="bd-btn" onClick={()=>openDecision(d.id)}>Open full record on Board</button>
  </section>;
}
