import { useEffect, useRef, useState } from 'react';
import { runtime } from '../lib/api';
import { useRuntimeState, useActiveChannel } from '../lib/room';

type Goal = { id: string; text: string; owner: string; status: string; evidence: string; rounds: number; max_rounds: number };
export function WorkControls({ navigate }: { navigate: (view: 'board' | 'team') => void }) {
  const { data, refresh } = useRuntimeState();
  const goal = (data as (typeof data & { goal?: Goal }))?.goal;
  const channel = useActiveChannel();
  const [dialog, setDialog] = useState<'goal' | 'task' | ''>('');
  const modal = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (dialog) modal.current?.showModal(); }, [dialog]);
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [owner, setOwner] = useState('');
  const [rounds, setRounds] = useState(12);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const openGoal = (e: Event) => {
      const command = ((e as CustomEvent<string>).detail || '').trim();
      setText(['status', 'pause', 'resume', 'cancel'].includes(command) ? '' : command); setError(''); setDialog('goal');
      if (['pause', 'resume', 'cancel'].includes(command)) {
        setBusy(true);
        void runtime<Goal | null>('/goal').then(current => {
          if (!current) throw new Error('No shared goal exists.');
          return runtime('/goal', { action: command, goal_id: current.id });
        }).then(() => refresh()).catch(e => setError(e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
      }
    };
    const openTask = (e: Event) => { const d = (e as CustomEvent<{ text: string; id: string }>).detail; setText(`From chat #${d.id}:\n${d.text}`.slice(0, 3000)); setTitle(d.text.split('\n')[0].slice(0, 120)); setError(''); setDialog('task'); };
    window.addEventListener('workspace-goal', openGoal);
    window.addEventListener('workspace-create-task', openTask);
    return () => { window.removeEventListener('workspace-goal', openGoal); window.removeEventListener('workspace-create-task', openTask); };
  }, []);
  async function change(action: string) {
    if (busy) return;
    setBusy(true); setError('');
    try { await runtime('/goal', { action, goal_id: goal?.id }); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const agents = Object.entries(data?.agents ?? {}).filter(([, a]) => !a.archived);
  return <>
    <div className="workspace-goalbar">
      {goal ? <><strong>Goal · {goal.status}</strong><span title={goal.evidence || goal.text}>{goal.text}</span><small>{goal.rounds}/{goal.max_rounds} rounds · @{goal.owner}</small>
        {goal.status === 'active' && <button disabled={busy} onClick={() => void change('pause')}>Pause goal</button>}
        {['paused', 'blocked'].includes(goal.status) && <button disabled={busy} onClick={() => void change('resume')}>Resume goal</button>}
        {!['complete', 'cancelled'].includes(goal.status) && <button disabled={busy} onClick={() => void change('cancel')}>Cancel goal</button>}
        <button onClick={() => { setText(''); setDialog('goal'); }}>Details</button>
      </> : <span>Shared goals keep the team working toward an explicit outcome.</span>}
      {(!goal || ['complete', 'cancelled'].includes(goal.status)) && <button onClick={() => { setText(''); setError(''); setDialog('goal'); }}>Set goal</button>}
      {error && !dialog && <span role="alert">{error}</span>}
    </div>
    {dialog && <>
      <dialog ref={modal} onCancel={() => setDialog('')} aria-modal="true" aria-labelledby="work-dialog-title" className="workspace-work-dialog" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape' && !busy) setDialog(''); }}>
        <h2 id="work-dialog-title">{dialog === 'goal' ? 'Shared goal' : 'Create Board task'}</h2>
        {dialog === 'goal' && goal && !['complete', 'cancelled'].includes(goal.status) ? <><p>{goal.text}</p><p>Status: {goal.status}. {goal.evidence}</p><p>Use the goal bar to pause, resume or cancel this goal before starting another.</p></> : <form onSubmit={async e => {
          e.preventDefault(); if (busy) return; setBusy(true); setError('');
          try {
            if (dialog === 'goal') await runtime('/goal', { action: 'start', text, owner, channel, max_rounds: rounds });
            else await runtime('/tasks', { action: 'create', title, details: text, owner, channel, start: false });
            await refresh(); setDialog(''); if (dialog === 'task') navigate('board');
          } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
        }}>
          {dialog === 'task' && <label>Title<input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} required /></label>}
          <label>{dialog === 'goal' ? 'Outcome and done criteria' : 'Details'}<textarea autoFocus required value={text} onChange={e => setText(e.target.value)} rows={5} maxLength={dialog === 'goal' ? 2000 : 3000} /></label>
          <label>{dialog === 'goal' ? 'Goal lead' : 'Owner'}<select value={owner} onChange={e => setOwner(e.target.value)} required={dialog === 'goal'}><option value="">{dialog === 'goal' ? 'Choose an agent' : 'Unassigned'}</option>{agents.map(([id, a]) => <option value={id} key={id}>{a.label || id}</option>)}</select></label>
          {dialog === 'goal' && <><label>Maximum continuation rounds<input type="number" min={1} max={100} value={rounds} onChange={e => setRounds(Number(e.target.value))} /></label><p>Existing conversation limits also apply. Stops, failures and restarts pause the goal. The lead records completion evidence.</p></>}
          <button className="workspace-primary" disabled={busy}>{busy ? 'Saving…' : dialog === 'goal' ? 'Start goal' : 'Create task'}</button>
        </form>}
        {error && <p role="alert">{error}</p>}<button disabled={busy} onClick={() => setDialog('')}>Close</button>
      </dialog>
    </>}
  </>;
}
