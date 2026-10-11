import { memo, useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { api, errorMessage } from '../lib/api';
import { useRoomEvent, useRuntimeState } from '../lib/room';
import type { AgentState } from '../lib/types';
import { legacy } from '../legacy';
import { decisionURL, openDecision, type Decision, type Summary, type Stage, type Contribution } from './model';
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

const steps = ['Proposals', 'Compare', 'Recommendation', 'Your decision'];
const stepFor: Partial<Record<Stage, number>> = {positions:0, critique:1, proposal:2, verdicts:2, review:3};
type Agents = Record<string, AgentState>;
const nameFor = (id:string, agents:Agents) => agents[id]?.label || id;
const samePeople = (a:string[],b:string[]) => a.length===b.length && a.every(id=>b.includes(id));
const currentRound = (d:Decision) => d.contributions.filter(c=>(c.round??1)===(d.round??1));
// This is sealed_rounds()'s current-round boundary, confirmed by the API owner.
// Individual contribution.sealed still fences every text/evidence/attachment below.
const isRevealed = (d:Decision) => d.stage!=='draft' && d.stage!=='positions';

// No participation props: a submission or routine refresh cannot replay stage motion.
const StageBar = memo(function StageBar({stage}:{stage:Stage}) {
  const step=stepFor[stage];
  if(step===undefined)return <p className="decision-stage-label">{labels[stage]}</p>;
  return <div className="decision-step-row"><ol className="decision-step-bar" aria-hidden="true" key={stage}>
    {steps.map((label,i)=><li key={label} className={i<step?'is-done':i===step?'is-current':''}/>)}</ol>
    <span className="decision-stage-label">{steps[step]} <span>· step {step+1} of 4</span></span>
  </div>;
});

function useProgressEvents(d:Decision|null, agents:Agents) {
  const previous=useRef<{id:string;round:number;stage:Stage;submitted:string[]}|null>(null);
  const [pulse,setPulse]=useState<string[]>([]),[tick,setTick]=useState(0),[announcement,setAnnouncement]=useState('');
  useEffect(()=>{
    if(!d){previous.current=null;return;}
    const next={id:d.id,round:d.round??1,stage:d.stage,submitted:d.coverage?.submitted || []},old=previous.current;
    previous.current=next;
    if(!old || old.id!==next.id || old.round!==next.round){setPulse([]);setAnnouncement('');return;}
    const added=next.submitted.filter(id=>!old.submitted.includes(id)),messages=[];
    if(added.length){setPulse(added);setTick(v=>v+1);messages.push(`${added.map(id=>nameFor(id,agents)).join(', ')} submitted. ${next.submitted.length} of ${d.coverage?.invited.length ?? d.participants.length} proposals in.`);}
    if(old.stage!==next.stage)messages.push(`Decision moved to ${steps[stepFor[next.stage] ?? -1] || labels[next.stage]}.`);
    if(messages.length)setAnnouncement(messages.join(' '));
  },[d,agents]);
  useEffect(()=>{if(!pulse.length)return;const timer=setTimeout(()=>setPulse([]),200);return()=>clearTimeout(timer);},[pulse]);
  return {pulse,tick,announcement};
}

function Avatar({id,agents,submitted,pulse=false}:{id:string;agents:Agents;submitted:boolean;pulse?:boolean}) {
  return <span aria-hidden="true" className={`decision-avatar ${submitted?'is-submitted':'is-missing'}${pulse?' is-new':''}`}
    style={{'--agent-color':agents[id]?.color || 'var(--accent)'} as CSSProperties}>{nameFor(id,agents).charAt(0).toUpperCase()}</span>;
}

function Progress({d,agents,announce=false}:{d:Decision;agents:Agents;announce?:boolean}) {
  const count=d.coverage,{pulse,tick,announcement}=useProgressEvents(d,agents),step=steps[stepFor[d.stage] ?? -1] || labels[d.stage];
  const waiting=count && samePeople(d.owed,count.missing) ? '' : `${step}: `;
  return <><StageBar stage={d.stage}/>
    {count && <div className="decision-participation"><span className="decision-avatar-stack" aria-hidden="true">{count.invited.map(id=><Avatar key={id} id={id} agents={agents} submitted={count.submitted.includes(id)} pulse={pulse.includes(id)}/>)}</span>
      <p><span className={tick?'decision-count is-new':'decision-count'} key={tick}>{count.submitted.length} of {count.invited.length}</span> proposals in{d.owed.length ? ` · ${waiting}waiting on ${d.owed.map(id=>nameFor(id,agents)).join(', ')}` : ''}</p></div>}
    {announce&&<p className="decision-sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>}
    {d.interrupted && <p className="decision-warning">{d.interrupted}</p>}
    {(d.phase==='review_ready'||d.phase==='recorded') && <p>Outcome: {d.outcome}</p>}
  </>;
}

export default function ChatDecision({summary,project}:{summary:Summary;project:string}) {
  const {data,error}=useDecision(summary.id,project);
  const {data:room}=useRuntimeState(),agents=room?.agents || {};
  const [host]=useState(()=>document.createElement('section'));
  useEffect(()=>{host.className='chat-decision-host';host.setAttribute('aria-label','Current team decision');legacy.header.after(host);return()=>host.remove();},[host]);
  return createPortal(<article className="chat-decision-card">
    <div className="decision-card-content"><span className="decision-eyebrow">TEAM DECISION <span>· Round {data?.round ?? 1} · led by {nameFor(data?.lead || summary.lead,agents)}</span></span><strong>{data?.question || summary.question}</strong>
      {data?<Progress d={data} agents={agents} announce/>:<p role="status">{labels[summary.stage]} · Loading participation…</p>}
      {error&&<p className="decision-error" role="alert">Could not refresh decision: {error}</p>}
    </div><button className="bd-btn" onClick={e=>window.dispatchEvent(new CustomEvent('workspace-side-panel',{detail:{tab:'decision',decision_id:summary.id,opener:e.currentTarget}}))}>Open details</button>
  </article>,host);
}

export function DecisionPanel({id,project,active,close}:{id:string;project:string;active:boolean;close:()=>void}) {
  const {data:d,error}=useDecision(id,project,active),heading=useRef<HTMLHeadingElement>(null),focused=useRef(false);
  const {data:room}=useRuntimeState(),agents=room?.agents || {};
  const {pulse}=useProgressEvents(d,agents);
  useEffect(()=>{if(active&&d&&!focused.current){heading.current?.focus();focused.current=true;}if(!active)focused.current=false;},[active,d]);
  if(!d)return <section className={`decision-dock${active?' is-active':''}`}><div className="decision-dock-title"><span className="decision-eyebrow">TEAM DECISION</span><button className="bd-btn decision-close" aria-label="Close decision details" onClick={close}>Close</button></div><p role={error?'alert':'status'}>{error || 'Loading decision…'}</p></section>;
  const current=currentRound(d),revealed=isRevealed(d),step=steps[stepFor[d.stage] ?? -1] || labels[d.stage];
  const invited=d.coverage?.invited || d.participants,submitted=d.coverage?.submitted || [];
  return <section className={`decision-dock${active?' is-active':''}`} aria-label="Decision details">
    <div className="decision-dock-title"><span className="decision-eyebrow">TEAM DECISION <span>· Round {d.round??1}</span></span><button className="bd-btn decision-close" aria-label="Close decision details" onClick={close}>Close</button></div>
    <h2 ref={heading} tabIndex={-1}>{d.question}</h2><div className="decision-dock-progress"><Progress d={d} agents={agents}/></div>
    {error&&<p className="decision-error" role="alert">Could not refresh: {error}</p>}
    {!!d.inputs?.length && <section><h3>Your input</h3>{d.inputs.map((input,i)=><blockquote key={i}><strong>{input.kind==='requirement'?'Requirement':'Suggestion'}</strong><p>{input.quote}</p></blockquote>)}</section>}
    <section><h3>This round</h3><ul className="decision-people">{invited.map((person,i)=><li key={person}>
      <Avatar id={person} agents={agents} submitted={submitted.includes(person)} pulse={pulse.includes(person)}/><span className="decision-person-name">{nameFor(person,agents)}</span>
      <span key={String(revealed)} className={`decision-person-chips${revealed?' is-revealed':''}`} style={{'--reveal-delay':`${Math.min(i,4)*40}ms`} as CSSProperties}>
        {revealed ? <><span className="decision-chip">{submitted.includes(person)?'Proposal revealed':'No proposal'}</span>{stepFor[d.stage]!==undefined&&<span className={`decision-chip${d.owed.includes(person)?' is-owed':' is-complete'}`}>{step}: {d.owed.includes(person)?'not yet':'done'}</span>}</> : <span className={`decision-chip${submitted.includes(person)?' is-complete':' is-owed'}`}>{submitted.includes(person)?<><span aria-hidden="true">🔒 </span>Submitted, hidden until all are in</>:'Not yet'}</span>}
      </span></li>)}</ul>
      {revealed&&submitted.length===0&&<p className="decision-muted">No proposals submitted.</p>}
      {current.filter(c=>!c.sealed && (c.kind!=='position'||revealed)).map(c=><details className="decision-contribution" key={c.id}>
        <summary>{nameFor(c.author,agents)} · {c.kind}</summary><ContributionContent c={c}/>
      </details>)}
    </section>
    {!!d.rounds?.length&&<details><summary>Earlier rounds</summary>{d.rounds.map(round=><div key={round.round}><h4>Round {round.round}</h4><p>{round.revealed?'Proposals revealed':'Proposals remain sealed'} · {round.concluded?`Recommendation: ${round.outcome}`:'No recommendation recorded'}</p><p>{round.reason}</p>
      {round.revealed&&d.contributions.filter(c=>(c.round??1)===round.round&&!c.sealed).map(c=><p className="decision-text" key={c.id}>{nameFor(c.author,agents)}: {c.text || c.reason}</p>)}
    </div>)}</details>}
    <footer className="decision-dock-footer"><span>Keep discussing in chat.</span><a href={decisionURL(d.id)} onClick={e=>{if(!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey){e.preventDefault();openDecision(d.id);}}}>Full record on Board →</a></footer>
  </section>;
}

function ContributionContent({c}:{c:Contribution}) {
  return <><p className="decision-text">{c.text || c.reason || 'Status recorded'}</p>
    {!!c.evidence?.length&&<ul>{c.evidence.map((text,i)=><li key={i}>{text}</li>)}</ul>}
    {c.attachments?.filter(a=>/^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(a.url)).map(a=><a key={a.url} href={a.url} target="_blank" rel="noreferrer">{a.name || 'View attachment'}</a>)}
  </>;
}
