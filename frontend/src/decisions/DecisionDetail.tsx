import { useEffect, useId, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { RunInfo } from '../lib/types';
import type { Agents } from '../board/shared';
import { useStoreValue } from '../lib/room';
import { contextLabel, notePreview, proposalFor, stageIndex, stages, votesFor, type Contribution, type Decision, type Listing } from './model';
import { DecisionRecommendation, DecisionChoices, DecisionMedia, decisionName } from './ChatDecision';

function ContributionCard({c, label}: {c: Contribution; label: (name: string) => string}) {
  return <article className="decision-contribution"><h3>{label(c.author)} · {c.kind === 'missing' ? 'No response' : c.kind}</h3>
    <p className="decision-muted">Revision {c.revision} · {c.id}{c.context && ` · ${contextLabel(c.context)}`}{c.before_new_direction && ' · Before new direction'}</p>
    <p className="decision-text">{c.text || c.reason || `No response from ${label(c.author)}`}</p>
    <DecisionMedia c={c}/>
    {!!c.evidence?.length && <div><strong>Evidence</strong><ul>{c.evidence.map((s,i)=><li key={i}>{s}</li>)}</ul></div>}
    {!!c.responds_to?.length && <p className="decision-muted">Responds to {c.responds_to.join(', ')}</p>}
  </article>;
}
const NO_CHATS: {id: string}[] = [];

export default function DecisionDetail({d, listing, active, agents, runs, receive, onTask}: {
  d: Decision; listing: Listing | null; active: boolean; agents: Agents; runs: RunInfo[];
  receive: (d: Decision) => void; onTask: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [announcement, setAnnouncement] = useState('');
  const chats = useStoreValue<{id: string}[]>('chatSessions', NO_CHATS);
  const chatAvailable = chats.some(c => c.id === d.channel);
  const [mode, setMode] = useState<'edit'|'return'|'close_stage'|'stop'|''>(''), [draft, setDraft] = useState('');
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef<HTMLElement | null>(null), heading = useRef<HTMLHeadingElement>(null);
  const alive = useRef(true), pending = useRef(false), current = useRef(d), formToken = useRef(''); current.current = d;
  const id = useId(), candidate = proposalFor(d), proposal = candidate?.sealed ? undefined : candidate;
  const votes = proposal ? votesFor(d) : {}, label = (name: string) => decisionName(name, agents);
  const token = JSON.stringify(d.token), allowed = (action: string) => d.actions.includes(action);
  const recorded = !!d.record?.note_id;
  const tasks: {title: string; details: string; id?: string; error?: string}[] = recorded ? d.record.tasks || [] : proposal?.tasks || [];
  
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {if (active && heading.current?.offsetParent) heading.current.focus();}, [d.stage, d.revision, active]);
  const act = async (action: string, body: Record<string,unknown> = {}, expected = token) => {
    if (pending.current || !alive.current) return false;
    if (JSON.stringify(current.current.token) !== expected || !current.current.actions.includes(action)) {setError('The discussion changed. Review its current stage before applying this action; your draft is kept.'); return false;}
    pending.current = true; setBusy(true); setError('');
    try {const next = await api<Decision>('/api/decisions/' + encodeURIComponent(d.id) + '/' + action, body);
      if (!alive.current) return false;
      if (next.id !== d.id) throw new Error('Response belongs to a different discussion. Refresh before continuing.');
      receive(next); setAnnouncement(action === 'record' ? next.record?.note_id ? 'Decision saved. Check each follow-up task below.' : 'Recording returned without a saved decision.' : action === 'stop' ? 'Discussion stopped. Complete submissions are preserved.' : 'Discussion updated.'); return true;
    } catch (e) {if (alive.current) setError(errorMessage(e)); return false;}
    finally {pending.current = false; if (alive.current) setBusy(false);}
  };
  const show = (next: typeof mode, event: {currentTarget: HTMLElement}) => {
    opener.current = event.currentTarget; formToken.current = token; setError('');
    if (next !== mode) setDraft(next === 'edit' ? proposal?.text || '' : '');
    setMode(next); dialog.current?.showModal();
  };
  const transcript = () => {
    window.switchChannel?.(d.channel);
    if (window.activeChannel !== d.channel) {setError('The discussion chat is unavailable. Wait for the chat list or reopen it from the sidebar; this review stays here.'); return;}
    const url = new URL(location.href); url.searchParams.set('view','chat');
    for (const key of ['decision','task','project','history']) url.searchParams.delete(key);
    history.pushState({},'',url.pathname + url.search); window.dispatchEvent(new PopStateEvent('popstate'));
  };
  const skipTitle = d.stage === 'verdicts' ? 'End unresolved' : `Continue without ${d.owed.map(label).join(', ') || 'further responses'}`;
  const slots = listing?.chat_slots;
  const slotsFull = !!slots && slots.used >= slots.limit;
  const status = d.stage === 'recorded' ? 'Decision recorded' : recorded ? 'Decision saved · follow-up task recovery' : d.stage === 'stopped' ? 'Stopped · submissions preserved' : d.interrupted ? d.interrupted : d.stage === 'review' ? `Review ready · ${d.outcome === 'agreed' ? 'All participants agreed' : d.outcome === 'dissent' ? 'Dissent remains' : 'Unresolved outcome'}` : stages[stageIndex[d.stage]];
  return <div className="decision-detail">
    <header className="decision-heading"><div><h2 ref={heading} tabIndex={-1}>{d.question}</h2><p>Round {d.round || 1} · led by {label(d.lead)}</p></div>{d.channel && <div><button className="bd-btn" disabled={!chatAvailable} aria-describedby={!chatAvailable ? id + '-chat' : undefined} onClick={transcript}>Open discussion transcript</button>{!chatAvailable && <p id={id + '-chat'} className="decision-muted">Wait for the chat list, or reopen this discussion chat from the sidebar.</p>}</div>}</header>
    <details><summary>Discussion progress</summary><ol className="decision-stages" aria-label="Discussion stages">{stages.map((s,i)=><li key={s} aria-current={stageIndex[d.stage] === i ? 'step' : undefined}>{i+1}. {s}</li>)}</ol></details>
    <div className="decision-banner" role="status"><strong>{status}</strong></div><p className="decision-sr-only" role="status">{announcement}</p>
    {error && <p className="decision-error" role="alert">{error}</p>}
    <DecisionRecommendation d={d} agents={agents} compact/><DecisionChoices key={d.id} d={d} receive={receive}/><details open className="decision-full-recommendation"><summary>Full recommendation and responses</summary><DecisionRecommendation d={d} agents={agents}/></details>
    <details className="decision-card"><summary>Original question and criteria</summary><ul>{d.criteria.map((c,i)=><li key={i}>{c}</li>)}</ul><p className="decision-muted">Participants: {d.participants.map(label).join(', ')}.</p></details>
    {allowed('start') && <section className="decision-card"><h3>Saved draft</h3><p>No participant work has started.</p>
      <p id={id + '-slots'}>Starting opens one dedicated discussion chat. {slots ? `${slots.used} of ${slots.limit} chat slots used.` : listing ? 'Chat capacity is unavailable. The server checks capacity when starting.' : 'Checking chat capacity...'}</p>
      {slotsFull && <p>All chat slots are used. Close a chat before starting; this draft stays saved.</p>}
      <button className="bd-btn bd-btn-primary" disabled={busy || !listing || slotsFull} aria-describedby={id + '-slots'} onClick={() => void act('start')}>Start discussion</button></section>}
    {allowed('continue') && <section className="decision-card"><h3>Interrupted discussion</h3><p>Complete submissions stay attributed. Continue repeats this stage at the new direction; earlier answers do not count as fresh answers.</p><button className="bd-btn bd-btn-primary" disabled={busy} onClick={() => void act('continue')}>Continue with new direction</button></section>}
    {d.owed.length > 0 && !recorded && <section className="decision-card"><h3>Waiting for responses</h3>
      {d.participants.map(p => {
        const owed = d.owed.includes(p), turn = d.turns[p], running = owed && runs.some(r => r.agent === p && r.channel === d.channel);
        const submitted = d.contributions.some(c => c.author === p && c.stage === d.stage && c.revision === d.revision && c.epoch === d.epoch && c.kind !== 'missing');
        return <div className="decision-wait" key={p}><div><strong>{label(p)}</strong><p>{!owed ? submitted ? 'Submitted this stage' : 'Not required this stage' : d.interrupted ? 'Paused for new direction' : running ? 'Running' : turn?.state === 'queued' ? 'Queued' : 'No response yet'}{owed && turn?.detail && ` · ${turn.detail}`}</p></div>
          {allowed('retry') && owed && turn?.state !== 'queued' && <button className="bd-btn" disabled={busy} onClick={() => void act('retry',{participant:p})}>Retry {label(p)}</button>}</div>;
      })}
      {d.deadline && <p className="decision-muted">Stage deadline {new Date(d.deadline*1000).toLocaleString()}. Complete responses are retained.</p>}
      {allowed('close_stage') && <button className="bd-btn" disabled={busy} onClick={e => show('close_stage',e)}>{skipTitle}</button>}
    </section>}
    <details className="decision-card"><summary>Recommendation details and individual responses</summary><div className="decision-layout">
      <section className="decision-card"><h3>Team proposal{proposal && ` · revision ${d.revision}`}</h3>{proposal ? <>
        <p className="decision-muted">{proposal.id} · by {label(proposal.author)}</p><p className="decision-text">{proposal.text}</p>
        {!!proposal.agreed?.length && <><h4>Agreed points</h4><ul>{proposal.agreed.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
        {!!proposal.dissent?.length && <><h4>Remaining dissent</h4><ul>{proposal.dissent.map((s,i)=><li key={i}>{s.point} · {s.by.map(label).join(', ')}</li>)}</ul></>}
        {!!proposal.unresolved?.length && <><h4>Unresolved questions</h4><ul>{proposal.unresolved.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
        {!!proposal.would_change?.length && <><h4>Evidence that could change the outcome</h4><ul>{proposal.would_change.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
        {!!proposal.answers?.length && <><h4>Lead’s challenge responses</h4><ul>{proposal.answers.map(a=><li key={a.challenge_id}>{a.challenge_id} · {a.status} · {a.note}</li>)}</ul></>}
      </> : <p>No proposal has been submitted. Initial positions and challenges stay in the contribution trail.</p>}
      {!!d.unanswered_challenges?.length && <div className="decision-warning"><h4>Not answered by the lead</h4>{d.unanswered_challenges.map(cid=>{const c=d.contributions.find(c=>c.id===cid); return <p key={cid}>{cid} · {c && `${label(c.author)}: ${c.text}`}</p>;})}</div>}
      </section>
      <section className="decision-card"><h3>Responses to {proposal ? `proposal ${proposal.id}` : 'the proposal'}</h3>{d.participants.map(p => {
        const vote = votes[p], position = [...d.contributions].reverse().find(c=>c.author===p&&c.kind==='position'&&!c.before_new_direction);
        return <article className="decision-verdict" key={p}><h4>{label(p)} · <span>{vote ? vote.verdict === 'agree' ? 'Agrees' : 'Dissents' : 'No verdict'}</span></h4>
          <p className="decision-muted">{position ? contextLabel(position.context) : 'No initial position'}{vote && ` · revision ${vote.revision} · ${vote.proposal_id}`}</p><p className="decision-text">{vote?.reason || (proposal ? 'No response to this proposal revision.' : 'Waiting for a proposal.')}</p>
          {d.contributions.some(c=>c.author===p&&c.kind==='missing') && <p className="decision-warning">Skipped stages: {[...new Set(d.contributions.filter(c=>c.author===p&&c.kind==='missing').map(c=>c.stage))].join(', ')}</p>}
        </article>;
      })}</section>
    </div></details>
    <details className="decision-card decision-record"><summary>{recorded ? 'Saved outcome and tasks' : 'Proposed follow-up tasks'}</summary>
      {!tasks.length&&<p>No follow-up tasks.</p>}{tasks.map((task,i)=><article className="decision-task" key={i}><strong>{task.title}</strong><p className="decision-text">{task.details}</p>{task.id&&<button className="bd-btn" onClick={()=>onTask(task.id!)}>Open task</button>}{task.error&&<p role="alert">{task.error}</p>}</article>)}
      <details><summary>Record text</summary><pre>{notePreview(d)}</pre></details>
    </details>
    <details className="decision-card decision-trail"><summary>See how the team got here · {d.contributions.length} contributions</summary>{d.contributions.map(c=>c.sealed?<p key={c.id}>{label(c.author)} submitted a hidden proposal.</p>:<ContributionCard key={c.id} c={c} label={label} />)}</details>
    <dialog ref={dialog} className="decision-dialog" aria-labelledby={id + '-dialog'} onCancel={e=>{if(busy)e.preventDefault();}} onClose={()=>opener.current?.focus()}>
      <form onSubmit={async e=>{e.preventDefault();if((mode==='edit'||mode==='return')&&!draft.trim()){setError('Enter the text before continuing.');return;}
        const ok=await act(mode,mode==='edit'?{text:draft}:mode==='return'?{question:draft}:{},formToken.current);if(ok)dialog.current?.close();}}>
        <h2 id={id + '-dialog'}>{mode==='edit'?'Edit proposal':mode==='return'?'Return with a question':mode==='stop'?'Stop this discussion?':`Continue this discussion without ${d.owed.map(label).join(', ')}?`}</h2>
        {mode==='edit'||mode==='return'?<><p>This creates revision {d.revision+1} and requires fresh verdicts. Existing contributions remain attributed.</p><label htmlFor={id + '-draft'}>{mode==='edit'?'Revised proposal':'Question for the lead'}</label><textarea id={id + '-draft'} autoFocus value={draft} maxLength={mode==='edit'?8000:2000} onChange={e=>setDraft(e.target.value)} required /></>
          : <p>{mode==='stop'?'Complete submissions stay saved. Queued submissions will no longer be accepted.':`These participants will be recorded as missing for ${d.stage}. Missing replies never count as agreement.${d.stage==='verdicts'?' You will review the unresolved outcome before recording.':''}`}</p>}
        {token!==formToken.current && <p className="decision-warning">The discussion changed while this dialog was open. Cancel and review the current stage; the entered text is retained.</p>}
        {error&&<p role="alert" className="decision-error">{error}</p>}
        <div className="decision-actions"><button type="button" autoFocus={mode!=='edit'&&mode!=='return'} className="bd-btn" disabled={busy} onClick={()=>dialog.current?.close()}>Cancel</button><button className="bd-btn bd-btn-primary" disabled={busy||token!==formToken.current||!allowed(mode)}>{busy?'Applying...':mode==='edit'?'Save revised proposal':mode==='return'?'Send question':mode==='stop'?'Stop discussion':skipTitle}</button></div>
      </form>
    </dialog>
  </div>;
}
