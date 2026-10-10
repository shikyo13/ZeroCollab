import { useEffect, useId, useRef, useState } from 'react';
import { runtime } from '../lib/api';
import { useRuntimeState, useActiveChannel } from '../lib/room';
import { EVENTS, LIMITS } from '../lib/constants';

type Goal = { id: string; text: string; owner: string; status: string; evidence: string; rounds: number; max_rounds: number };
type WorkDialog = { kind: 'goal' | 'task'; inspected: Goal | null };
const isOpen = (goal: Goal | null | undefined): goal is Goal => !!goal && !['complete', 'cancelled'].includes(goal.status);

export function WorkControls({ navigate }: { navigate: (view: 'board' | 'team') => void }) {
  const { data, refresh } = useRuntimeState();
  const goal = (data as (typeof data & { goal?: Goal }))?.goal;
  const channel = useActiveChannel();
  const [dialog, setDialog] = useState<WorkDialog | null>(null);
  const modal = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const generation = useRef(0);
  const pending = useRef(false);
  const id = useId();
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [owner, setOwner] = useState('');
  const [rounds, setRounds] = useState(12);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  function close() {
    generation.current += 1;
    modal.current?.close();
    setDialog(null);
    const target = opener.current;
    queueMicrotask(() => {
      if (target?.isConnected && !target.matches(':disabled') && target.getClientRects().length) target.focus();
      else document.getElementById('input')?.focus();
    });
  }
  function open(next: WorkDialog) {
    generation.current += 1;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setError('');
    setDialog(next);
  }
  // An inspected goal never becomes a start form or another goal's controls.
  useEffect(() => {
    if (dialog?.kind === 'goal' && dialog.inspected && data &&
      (goal?.id !== dialog.inspected.id || !isOpen(goal))) close();
  }, [goal, data, dialog]);
  useEffect(() => {
    if (dialog && !modal.current?.open) {
      modal.current?.showModal();
      if (!dialog.inspected) modal.current?.querySelector('textarea')?.focus();
    }
  }, [dialog]);
  useEffect(() => () => { generation.current += 1; }, []);

  async function save(write: () => Promise<unknown>, after?: () => void) {
    if (pending.current) return;
    const started = generation.current;
    pending.current = true;
    setBusy(true); setError('');
    try {
      await write();
      await refresh();
      if (started === generation.current) { close(); after?.(); }
    } catch (e) {
      if (started === generation.current) setError(e instanceof Error ? e.message : String(e));
    } finally { pending.current = false; setBusy(false); }
  }
  useEffect(() => {
    const openGoal = (e: Event) => {
      if (pending.current) return;
      const command = ((e as CustomEvent<string>).detail || '').trim();
      const inspected = isOpen(goal) ? goal : null;
      setText(['status', 'pause', 'resume', 'cancel'].includes(command) ? '' : command);
      open({ kind: 'goal', inspected });
      if (['pause', 'resume', 'cancel'].includes(command)) {
        void save(async () => {
          if (!inspected) throw new Error('No open shared goal exists.');
          return runtime('/goal', { action: command, goal_id: inspected.id });
        });
      }
    };
    const openTask = (e: Event) => {
      if (pending.current) return;
      const d = (e as CustomEvent<{ text: string; id: string }>).detail;
      setText(`From chat #${d.id}:\n${d.text}`.slice(0, LIMITS.taskDetails));
      setTitle(d.text.split('\n')[0].slice(0, LIMITS.taskTitle));
      open({ kind: 'task', inspected: null });
    };
    window.addEventListener(EVENTS.goal, openGoal);
    window.addEventListener(EVENTS.createTask, openTask);
    return () => { window.removeEventListener(EVENTS.goal, openGoal); window.removeEventListener(EVENTS.createTask, openTask); };
  }, [goal, refresh]);

  const inspected = dialog?.inspected;
  const shown = inspected && goal?.id === inspected.id ? goal : inspected;
  const exhausted = !!shown && shown.rounds >= shown.max_rounds;
  const agents = Object.entries(data?.agents ?? {}).filter(([, a]) => !a.archived);
  function change(action: string) {
    if (shown) void save(() => runtime('/goal', { action, goal_id: shown.id }));
  }
  return dialog && <dialog ref={modal} onCancel={e => { e.preventDefault(); close(); }} aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={error ? `${id}-error` : undefined} className="workspace-work-dialog" onClick={e => e.stopPropagation()}>
    <div className="workspace-work-heading"><h2 id={`${id}-title`}>{dialog.kind === 'goal' ? 'Shared goal' : 'Create Board task'}</h2><button type="button" className="workspace-work-button" autoFocus={!!shown} onClick={close}>Close</button></div>
    {dialog.kind === 'goal' && shown ? <>
      <p className="workspace-goal-text">{shown.text}</p>
      <p>Status: <strong>{shown.status}</strong>{shown.evidence && <> · {shown.evidence}</>}</p>
      <p>{shown.rounds} of {shown.max_rounds} continuation rounds used · lead @{shown.owner}</p>
      {exhausted && <p id={`${id}-limit`} className="workspace-goal-limit" tabIndex={0}>Round limit reached ({shown.rounds} of {shown.max_rounds}). This goal cannot resume. Cancel it, then use /goal to start a new scoped goal.</p>}
      <div className="workspace-goal-actions">
        {shown.status === 'active' && <button className="workspace-work-button workspace-work-primary" disabled={busy} onClick={() => change('pause')}>Pause goal</button>}
        {['paused', 'blocked'].includes(shown.status) && <button className={`workspace-work-button${exhausted ? '' : ' workspace-work-primary'}`} disabled={busy || exhausted} aria-describedby={exhausted ? `${id}-limit` : undefined} onClick={() => change('resume')}>Resume goal</button>}
        <button className={`workspace-work-button ${exhausted ? 'workspace-work-primary' : 'workspace-work-danger'}`} disabled={busy} onClick={() => change('cancel')}>Cancel goal</button>
      </div>
      <p>Closing this dialog leaves the goal unchanged. Its updates remain in chat.</p>
    </> : <form onSubmit={e => {
      e.preventDefault();
      const kind = dialog.kind;
      void save(() => kind === 'goal'
        ? runtime('/goal', { action: 'start', text, owner, channel, max_rounds: rounds })
        : runtime('/tasks', { action: 'create', title, details: text, owner, channel, start: false }),
      kind === 'task' ? () => navigate('board') : undefined);
    }}>
      {dialog.kind === 'task' && <label>Title<input value={title} onChange={e => setTitle(e.target.value)} maxLength={LIMITS.taskTitle} required /></label>}
      <label>{dialog.kind === 'goal' ? 'Outcome and done criteria' : 'Details'}<textarea autoFocus required value={text} onChange={e => setText(e.target.value)} rows={5} maxLength={dialog.kind === 'goal' ? LIMITS.goal : LIMITS.taskDetails} /></label>
      <label>{dialog.kind === 'goal' ? 'Goal lead' : 'Owner'}<select value={owner} onChange={e => setOwner(e.target.value)} required={dialog.kind === 'goal'}><option value="">{dialog.kind === 'goal' ? 'Choose an agent' : 'Unassigned'}</option>{agents.map(([key, a]) => <option value={key} key={key}>{a.label || key}</option>)}</select></label>
      {dialog.kind === 'goal' && <><label>Maximum continuation rounds<input type="number" min={1} max={100} value={rounds} onChange={e => setRounds(Number(e.target.value))} /></label><p>Existing conversation limits also apply. Stops, failures and restarts pause the goal. The lead records completion evidence.</p></>}
      <button className="workspace-work-button workspace-work-primary" disabled={busy}>{busy ? 'Saving…' : dialog.kind === 'goal' ? 'Start goal' : 'Create task'}</button>
    </form>}
    {busy && <p role="status">Saving…</p>}
    {error && <p id={`${id}-error`} className="workspace-work-error" role="alert">{error}</p>}
  </dialog>;
}

/** Open goal details; new goals start from the chat's /goal command. */
export function GoalChip() {
  const { data } = useRuntimeState();
  const goal = (data as (typeof data & { goal?: Goal }))?.goal;
  if (!isOpen(goal)) return null;
  return <button className="ws-goal-chip" title={goal.text} aria-label={`Open shared goal details: ${goal.status}, ${goal.rounds} of ${goal.max_rounds} rounds used`} onClick={() => window.dispatchEvent(new CustomEvent(EVENTS.goal, { detail: 'status' }))}>
    Goal <b>{goal.status}</b> · {goal.rounds}/{goal.max_rounds}
  </button>;
}
