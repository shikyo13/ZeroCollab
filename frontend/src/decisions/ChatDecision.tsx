import { memo, useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { api, errorMessage } from '../lib/api';
import { useRoomEvent, useRuntimeState } from '../lib/room';
import type { AgentState, TaskAttachment } from '../lib/types';
import { readStored, writeStored } from '../lib/storage';
import { legacy } from '../legacy';
import { decisionURL, openDecision, proposalFor, votesFor, type Decision, type Summary, type Stage, type Contribution, type ProposalComment, type CommentField } from './model';
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
export const decisionName = (id:string, agents:Agents) => {
  const handle=agents[id]?.handle || id;
  return handle.charAt(0).toUpperCase()+handle.slice(1);
};
const nameFor = decisionName;
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
  </>;
}

/** The recommendation and its actual votes, kept separate from initial proposal attendance. */
export function DecisionRecommendation({d,agents,compact=false}:{d:Decision;agents:Agents;compact?:boolean}) {
  const p=proposalFor(d),proposal=p && !p.sealed ? p : null,votes=proposal?votesFor(d):{};
  const agrees=d.participants.filter(id=>votes[id]?.verdict==='agree'),dissent=d.participants.filter(id=>votes[id]?.verdict==='dissent');
  const missing=d.participants.filter(id=>!votes[id]),recorded=!!d.record?.note_id || d.stage==='recorded';
  const text=proposal?.text || '';
  const preview=text.length>140 ? text.slice(0,140)+'…' : text;
  return <section className={`decision-recommendation${compact?' is-compact':''}`}>
    <h3>{recorded?'Recorded recommendation':'Team recommendation'}</h3>
    {proposal ? <><p className="decision-text">{compact?preview:text}</p>
      <p className="decision-vote-count"><strong>{agrees.length} agree · {dissent.length} dissent · {missing.length} no verdict</strong></p>
      {!compact&&<><p className="decision-muted">Agree: {agrees.length?agrees.map(id=>nameFor(id,agents)).join(', '):'None yet'}.</p>
        {dissent.map(id=><p key={id} className="decision-warning">{nameFor(id,agents)} disagrees: {votes[id].reason || 'No reason supplied.'}</p>)}
        {!!missing.length&&<p className="decision-muted">No verdict: {missing.map(id=>nameFor(id,agents)).join(', ')}. Missing replies do not count as agreement.</p>}
        {!!proposal.unresolved?.length&&<><h4>Still open</h4><ul>{proposal.unresolved.map((point,i)=><li key={i}>{point}</li>)}</ul></>}
        {!!proposal.dissent?.length&&<><h4>Disagreements noted by the lead</h4><ul>{proposal.dissent.map((point,i)=><li key={i}>{point.point} · {point.by.map(id=>nameFor(id,agents)).join(', ')}</li>)}</ul></>}
      </>}
        {!compact&&!!proposal.agreed?.length&&<><h4>Agreed points</h4><ul>{proposal.agreed.map((point,i)=><li key={i}>{point}</li>)}</ul></>}
    </> : <p>{d.stage==='positions'||d.stage==='draft'?'The team is collecting proposals; no recommendation yet.':d.stage==='stopped'?'Discussion stopped without a current recommendation.':'No recommendation has been submitted. The discussion has not produced an answer yet.'}</p>}
    <p className="decision-human-status">{d.choice?`You ${d.choice.kind==='approve'?'approved':'rejected'} this proposal.`:recorded?'Outcome recorded.':d.stage==='review'?'Ready for your review.':d.stage==='stopped'?'Discussion ended.':'The team is discussing.'}</p>
    {!compact&&d.choice&&<details><summary>Your saved feedback</summary><p>{d.choice.note}</p>{d.choice.comments?.map((c,i)=><blockquote key={i}><strong>{c.point || c.field}</strong><p>{c.text}</p></blockquote>)}<DecisionMedia c={{...proposal!,id:'human-choice',sealed:false,attachments:d.choice.attachments}}/></details>}
  </section>;
}

export function DecisionChoices({d,receive}:{d:Decision;receive:(d:Decision)=>void}) {
  type Mode = 'approve'|'reject'|'return'|'edit'|'stop'|'feedback'|'';
  const [mode,setMode]=useState<Mode>(''),[draft,setDraft]=useState(''),[editText,setEditText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [comments,setComments]=useState<ProposalComment[]>([]),[attachments,setAttachments]=useState<TaskAttachment[]>([]);
  const [point,setPoint]=useState('text'),[comment,setComment]=useState('');
  const current=useRef(d),pending=useRef(false);current.current=d;
  const shown=useRef<{proposal_id:string;revision:number;epoch:number;token:string;proposal?:Contribution}|null>(null);
  const proposal=proposalFor(d),available=(action:string)=>d.actions.includes(action);
  const canFeedback=!d.choice&&(available('approve')||available('reject')||available('return'));
  const fence=()=>({proposal_id:d.current_proposal,revision:d.revision,epoch:d.epoch,token:JSON.stringify(d.token),proposal});
  const stale=!!shown.current&&(shown.current.proposal_id!==d.current_proposal||shown.current.revision!==d.revision||shown.current.epoch!==d.epoch||shown.current.token!==JSON.stringify(d.token));
  const begin=(next:Mode)=>{if(!shown.current)shown.current=fence();setMode(next);setError('');if(next==='edit')setEditText(proposal?.text || '');};
  const discard=()=>{shown.current=null;setDraft('');setEditText('');setComments([]);setAttachments([]);setComment('');setMode('');setError('');};
  const act=async(action:string)=>{
    if(pending.current)return;
    const bound=shown.current || fence();
    if(!available(action)||bound.proposal_id!==current.current.current_proposal||bound.revision!==current.current.revision||bound.epoch!==current.current.epoch||bound.token!==JSON.stringify(current.current.token)){
      setError('The proposal changed. Your feedback is kept with the earlier version. Discard it to review the new proposal.');return;
    }
    if((action==='edit'||action==='stop')&&(draft.trim()||comments.length||attachments.length||comment.trim())){setError('Keep or send your pending feedback before editing or ending the discussion. Discard feedback to continue.');return;}
    if(attachments.some(a=>(a.note || '').length>300)){setError('Shorten the image note to 300 characters before sending. The image is kept.');return;}
    if(comment.trim()){setError('Add your comment to the feedback before sending.');return;}
    const feedback={comments,attachments:attachments.map(a=>({url:a.url,name:a.name,source:a.source,note:a.note || ''}))};
    const identity={proposal_id:bound.proposal_id,revision:bound.revision,epoch:bound.epoch};
    const body=action==='approve'||action==='reject'?{...identity,note:draft,...feedback}:action==='return'?{...identity,question:draft,...feedback}:action==='edit'?{text:editText}:{};
    pending.current=true;setBusy(true);setError('');
    try{const next=await api<Decision>('/api/decisions/'+encodeURIComponent(d.id)+'/'+action,body);
      if(next.id!==current.current.id)return;
      receive(next);discard();
    }catch(e){setError(errorMessage(e));}finally{pending.current=false;setBusy(false);}
  };
  const feedbackProposal=shown.current?.proposal || proposal;
  const points=[{value:'text',label:'Recommendation text'},...(['agreed','dissent','unresolved','would_change','tasks'] as const).flatMap(field=>(feedbackProposal?.[field] || []).map((p,i)=>({value:field+':'+i,label:field.replace('_',' ')+' '+(i+1)+': '+(typeof p==='string'?p:'point' in p?p.point:p.title)})))];
  const addComment=()=>{if(!comment.trim()||comments.length>=12)return;const [field,index]=point.split(':');setComments(old=>[...old,{text:comment.trim(),field:field as CommentField,...(index!==undefined?{index:Number(index)}:{})}]);setComment('');};
  const saved=(a:TaskAttachment)=>{setAttachments(old=>old.length<6?[...old,a]:old);setMode('feedback');};
  return <section className="decision-human-actions"><h3>Your next step</h3>
    <div className="decision-actions">
      {available('approve')&&<button className="bd-btn bd-btn-primary" disabled={busy} onClick={()=>d.choice?.kind==='approve'?void act('approve'):begin('approve')}>{d.choice?.kind==='approve'?'Retry failed task creation':'Approve proposal'}</button>}
      {available('reject')&&<button className="bd-btn" disabled={busy} onClick={()=>begin('reject')}>Reject proposal</button>}
      {available('return')&&<button className="bd-btn" disabled={busy} onClick={()=>begin('return')}>Request changes</button>}
      {canFeedback&&<button className="bd-btn" disabled={busy} onClick={()=>begin('feedback')}>Comment</button>}
      {!available('approve')&&!available('reject')&&available('record')&&<button className="bd-btn bd-btn-primary" disabled={busy} onClick={()=>void act('record')}>Record outcome</button>}
      {available('edit')&&<button className="bd-btn" disabled={busy} onClick={()=>begin('edit')}>Edit recommendation</button>}
      {available('stop')&&<button className="bd-btn" disabled={busy} onClick={()=>begin('stop')}>End discussion…</button>}
      <button className="bd-btn" onClick={()=>{window.switchChannel?.(d.channel);const url=new URL(location.href);url.searchParams.set('view','chat');for(const key of ['decision','task','history','project'])url.searchParams.delete(key);history.pushState({},'',url);window.dispatchEvent(new PopStateEvent('popstate'));}}>Discuss in chat</button>
    </div>
    {mode&&<div className="decision-reply-form">
      <h4>{mode==='approve'?'Approve this proposal?':mode==='reject'?'Reject this proposal?':mode==='return'?'Request changes':mode==='edit'?'Edit recommendation':mode==='stop'?'End this discussion?':'Comments and annotations'}</h4>
      {mode==='approve'&&<p>Approval records your choice{proposal?.tasks?.length?` and creates ${proposal.tasks.length} follow-up tasks`:'. No follow-up tasks are proposed'}. The team votes stay as shown.</p>}
      {mode==='reject'&&<p>Rejection closes this proposal and creates no tasks. Use Request changes to ask the team to try again.</p>}
      {mode==='stop'?<p>Contributions stay saved.</p>:<label>{mode==='edit'?'Revised recommendation':mode==='return'?'What would you like changed?':'Your note (optional)'}<textarea aria-label={mode==='edit'?'Revised recommendation':mode==='return'?'What would you like changed?':'Your note (optional)'} maxLength={mode==='edit'?8000:2000} value={mode==='edit'?editText:draft} onChange={e=>mode==='edit'?setEditText(e.target.value):setDraft(e.target.value)}/></label>}
      {mode!=='stop'&&mode!=='edit'&&<><label>Comment on<select aria-label="Comment on" value={point} onChange={e=>setPoint(e.target.value)}>{points.map(p=><option value={p.value} key={p.value}>{p.label}</option>)}</select></label><label>Comment<textarea aria-label="Comment" maxLength={1000} value={comment} onChange={e=>setComment(e.target.value)}/></label><button className="bd-btn" disabled={!comment.trim()||comments.length>=12||busy||stale} onClick={addComment}>Add comment</button>
        <ul className="decision-feedback-list">{comments.map((c,i)=><li key={i}><strong>{points.find(p=>p.value===c.field+(c.index!==undefined?':'+c.index:''))?.label || c.field}</strong><p>{c.text}</p><button className="bd-btn" disabled={busy} onClick={()=>setComments(old=>old.filter((_,n)=>n!==i))}>Remove comment</button></li>)}</ul>
        {!!attachments.length&&<div className="decision-feedback-images">{attachments.map((a,i)=><figure key={a.url}><img src={a.url} alt={a.name}/><figcaption>{a.name}<label>Image note (up to 300 characters)<textarea aria-label="Image note (up to 300 characters)" maxLength={300} value={a.note || ''} onChange={e=>setAttachments(old=>old.map((item,n)=>n===i?{...item,note:e.target.value}:item))}/></label><button className="bd-btn" disabled={busy} onClick={()=>setAttachments(old=>old.filter((_,n)=>n!==i))}>Remove image</button></figcaption></figure>)}</div>}
      </>}
      {stale&&<p className="decision-warning">This proposal changed. Your feedback still belongs to the earlier version.</p>}
      <div className="decision-actions">
        {mode==='feedback'?(['return','approve','reject'] as const).filter(available).map(action=><button className="bd-btn" key={action} disabled={busy||stale||(action==='return'&&!draft.trim()&&!comments.length&&!attachments.length)} onClick={()=>begin(action)}>{action==='return'?'Send back to team':action==='approve'?'Review approval':'Review rejection'}</button>):<button className="bd-btn bd-btn-primary" disabled={busy||stale||(mode==='edit'?!editText.trim():mode==='return'&&!draft.trim()&&!comments.length&&!attachments.length)} onClick={()=>void act(mode)}>{busy?'Saving…':mode==='approve'?'Confirm approval':mode==='reject'?'Confirm rejection':mode==='return'?'Send to team':mode==='edit'?'Save recommendation':'End discussion'}</button>}
        <button className="bd-btn" disabled={busy} onClick={()=>setMode('')}>Cancel</button><button className="bd-btn" disabled={busy} onClick={discard}>Discard feedback</button>
      </div>
    </div>}
    {error&&<p className="decision-error" role="alert">{error}</p>}
    {proposal&&!proposal.sealed&&<DecisionMedia c={proposal} onSaved={canFeedback&&attachments.length<6&&!busy?(a=>{if(!shown.current)shown.current=fence();saved(a);}):undefined} onBegin={()=>{if(!shown.current)shown.current=fence();}}/>}
  </section>;
}

export default function ChatDecision({summary,project}:{summary:Summary;project:string}) {
  const {data,error}=useDecision(summary.id,project);
  const {data:room}=useRuntimeState(),agents=room?.agents || {};
  const [host]=useState(()=>document.createElement('section'));
  const key=`decision-hidden:${project}:${summary.id}`;
  const [hidden,setHidden]=useState(()=>readStored(key)==='1');
  const [inChat,setInChat]=useState(()=>(new URLSearchParams(location.search).get('view') || 'chat')==='chat');
  useEffect(()=>{const sync=()=>setInChat((new URLSearchParams(location.search).get('view') || 'chat')==='chat');window.addEventListener('popstate',sync);return()=>window.removeEventListener('popstate',sync);},[]);
  useEffect(()=>{host.className='chat-decision-host';host.setAttribute('aria-label','Current team decision');legacy.header.after(host);return()=>host.remove();},[host]);
  useEffect(()=>{host.hidden=!inChat||['recorded','stopped'].includes(data?.stage || summary.stage);},[host,inChat,data?.stage,summary.stage]);
  const hide=(value:boolean)=>{setHidden(value);writeStored(key,value?'1':'0');};
  if(hidden)return createPortal(<div className="decision-hidden"><button className="bd-btn" onClick={()=>hide(false)}>Show team decision</button><span>{summary.question}</span></div>,host);
  return createPortal(<article className="chat-decision-card">
    <div className="decision-card-content"><span className="decision-eyebrow">TEAM DECISION <span>· Round {data?.round ?? 1} · led by {nameFor(data?.lead || summary.lead,agents)}</span></span><strong>{data?.question || summary.question}</strong>
      {data?<><DecisionRecommendation d={data} agents={agents} compact/><details className="decision-compact-progress"><summary>Discussion progress</summary><Progress d={data} agents={agents} announce/></details></>:<p role="status">{labels[summary.stage]} · Loading participation…</p>}
      {error&&<p className="decision-error" role="alert">Could not refresh decision: {error}</p>}
    </div><div className="decision-card-controls"><button className="bd-btn" onClick={e=>window.dispatchEvent(new CustomEvent('workspace-side-panel',{detail:{tab:'decision',decision_id:summary.id,opener:e.currentTarget}}))}>Review proposal</button><button className="bd-btn" onClick={()=>hide(true)}>Hide</button></div>
  </article>,host);
}

export function DecisionPanel({id,project,active,close}:{id:string;project:string;active:boolean;close:()=>void}) {
  const {data:d,error}=useDecision(id,project,active),heading=useRef<HTMLHeadingElement>(null),focused=useRef(false);
  const {data:room}=useRuntimeState(),agents=room?.agents || {};
  const {pulse}=useProgressEvents(d,agents);
  useEffect(()=>{if(active&&d&&!focused.current){heading.current?.focus();focused.current=true;}if(!active)focused.current=false;},[active,d]);
  if(!d)return <section className={`decision-dock${active?' is-active':''}`}><div className="decision-dock-title"><span className="decision-eyebrow">TEAM DECISION</span><button className="bd-btn decision-close" aria-label="Close decision details" onClick={close}>Close</button></div><p role={error?'alert':'status'}>{error || 'Loading decision…'}</p></section>;
  const current=currentRound(d),revealed=isRevealed(d),step=steps[stepFor[d.stage] ?? -1] || labels[d.stage];
  const invited=d.coverage?.invited || d.participants,submitted=d.coverage?.submitted || [],votes=votesFor(d);
  return <section className={`decision-dock${active?' is-active':''}`} aria-label="Decision details">
    <div className="decision-dock-title"><span className="decision-eyebrow">TEAM DECISION <span>· Round {d.round??1}</span></span><button className="bd-btn decision-close" aria-label="Close decision details" onClick={close}>Close</button></div>
    <h2 ref={heading} tabIndex={-1}>{d.question}</h2><DecisionRecommendation d={d} agents={agents} compact/>
    <DecisionChoices d={d} receive={next=>window.Hub?.emit('decision',{type:'decision',data:next})}/><details open className="decision-full-recommendation"><summary>Full recommendation and responses</summary><DecisionRecommendation d={d} agents={agents}/></details>

    <details className="decision-dock-progress"><summary>Discussion progress</summary><Progress d={d} agents={agents}/></details>
    {error&&<p className="decision-error" role="alert">Could not refresh: {error}</p>}
    {!!d.inputs?.length && <details><summary>Your original request</summary>{d.inputs.map((input,i)=><blockquote key={i}><strong>{input.kind==='requirement'?'Requirement':'Suggestion'}</strong><p>{input.quote}</p></blockquote>)}</details>}
    <details><summary>Participants and discussion history</summary><ul className="decision-people">{invited.map((person,i)=><li key={person}>
      <Avatar id={person} agents={agents} submitted={submitted.includes(person)} pulse={pulse.includes(person)}/><span className="decision-person-name">{nameFor(person,agents)}</span>
      <span key={String(revealed)} className={`decision-person-chips${revealed?' is-revealed':''}`} style={{'--reveal-delay':`${Math.min(i,4)*40}ms`} as CSSProperties}>
        {revealed ? <><span className="decision-chip">{submitted.includes(person)?'Proposal revealed':'No proposal'}</span>{d.stage==='review'||d.stage==='recorded'?<span className={`decision-chip${votes[person]?.verdict==='agree'?' is-complete':' is-owed'}`}>{votes[person]?.verdict==='agree'?'Agrees':votes[person]?.verdict==='dissent'?'Dissents':'No verdict'}</span>:stepFor[d.stage]!==undefined&&<span className={`decision-chip${d.owed.includes(person)?' is-owed':' is-complete'}`}>{step}: {d.owed.includes(person)?'not yet':'submitted'}</span>}</> : <span className={`decision-chip${submitted.includes(person)?' is-complete':' is-owed'}`}>{submitted.includes(person)?<><span aria-hidden="true">🔒 </span>Submitted, hidden until all are in</>:'Not yet'}</span>}
      </span></li>)}</ul>
      {revealed&&submitted.length===0&&<p className="decision-muted">No proposals submitted.</p>}
      {current.filter(c=>!c.sealed && (c.kind!=='position'||revealed)).map(c=><details className="decision-contribution" key={c.id}>
        <summary>{nameFor(c.author,agents)} · {c.kind}</summary><ContributionContent c={c}/>
      </details>)}
    </details>
    {!!d.rounds?.length&&<details><summary>Earlier rounds</summary>{d.rounds.map(round=><div key={round.round}><h4>Round {round.round}</h4><p>{round.revealed?'Proposals revealed':'Proposals remain sealed'} · {round.concluded?`Recommendation: ${round.outcome}`:'No recommendation recorded'}</p><p>{round.reason}</p>
      {round.revealed&&d.contributions.filter(c=>(c.round??1)===round.round&&!c.sealed).map(c=><p className="decision-text" key={c.id}>{nameFor(c.author,agents)}: {c.text || c.reason}</p>)}
    </div>)}</details>}
    <footer className="decision-dock-footer"><span>Keep discussing in chat.</span><a href={decisionURL(d.id)} onClick={e=>{if(!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey){e.preventDefault();openDecision(d.id);}}}>Full record on Board →</a></footer>
  </section>;
}

function ContributionContent({c}:{c:Contribution}) {
  return <><p className="decision-text">{c.text || c.reason || 'Status recorded'}</p>
    {!!c.evidence?.length&&<ul>{c.evidence.map((text,i)=><li key={i}>{text}</li>)}</ul>}
    <DecisionMedia c={c}/>
  </>;
}

/** Revealed evidence only. Saved annotations stay tied to their source image. */
export function DecisionMedia({c,onSaved,onBegin}:{c:Contribution;onSaved?:(a:TaskAttachment)=>void;onBegin?:()=>void}) {
  if(c.sealed)return null;
  const images=(c.attachments || []).filter(a=>/^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]*\.(png|jpe?g|gif|webp)$/i.test(a.url)).map(a=>({...a,name:a.name || 'Proposal image'}));
  return <div className="decision-media">{images.map((a,i)=><figure key={a.url}><img src={a.url} alt={a.name || 'Proposal evidence'}/><figcaption>{a.name || 'Proposal image'}<div className="decision-actions"><button className="bd-btn" onClick={()=>{onBegin?.();window.ZeroImageReview?.(images,i,saved=>onSaved?.(saved));}}>Expand</button>{onSaved&&<button className="bd-btn" onClick={()=>{onBegin?.();window.ZeroAnnotate?.(a.url,onSaved,a.name);}}>Annotate image</button>}</div></figcaption></figure>)}</div>;
}
