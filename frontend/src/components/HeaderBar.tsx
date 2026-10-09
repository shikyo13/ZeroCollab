/* The single header row: where you are (chat or view, then project) on the left; the team
 * roster and the room's stop/resume on the right. Rendered into the legacy header so its
 * existing children (settings bar toggle, side-panel toggle) keep working. */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { runtime, errorMessage } from '../lib/api';
import { sessionTitle, useActiveChannel, useRuntimeState } from '../lib/room';
import type { RuntimeState } from '../lib/types';
import { legacy } from '../legacy';
import type { View } from '../workspace/useWorkspaceRoute';
import { Icon } from './Icon';
import { GoalChip } from './WorkControls';

const VIEW_TITLES: Record<View, string> = { projects: 'Projects', chat: 'Chat', board: 'Board', team: 'Team', activity: 'Activity' };
const STACK_SHOWN = 6;

/** Same wording as runtime.js's mode line, which now lives on the Activity page. */
function roomMode(s: RuntimeState): string {
  const capacity = s.limits.parallel_agents_effective;
  return `${s.paused ? 'Stopped' : 'Ready'} - ${capacity} agents can run together${s.limits.parallel_agents ? '' : ' (whole team)'}, `
    + `${s.limits.chat_handoffs ? `${s.limits.chat_handoffs} chat handoffs` : 'no handoff limit'}${s.automatic_tool_approvals ? ' - automatic tool approvals' : ''}`;
}

function usePopover() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); root.current?.querySelector('button')?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  return { open, setOpen, root };
}

function Roster({ state, onTeam }: { state: RuntimeState; onTeam: () => void }) {
  const { open, setOpen, root } = usePopover();
  const agents = Object.entries(state.agents).filter(([, a]) => !a.archived);
  const working = agents.filter(([, a]) => a.state === 'working').length;
  const mode = roomMode(state);
  return <div className="ws-roster" ref={root}>
    <button className="ws-roster-button" aria-expanded={open} aria-haspopup="dialog" title={mode}
      aria-label={`Team: ${agents.length} agents, ${working} working`} onClick={() => setOpen(!open)}>
      <span className="ws-avatars" aria-hidden="true">
        {agents.slice(0, STACK_SHOWN).map(([id, a]) => <span key={id} className={`ws-avatar${a.state === 'working' ? ' is-working' : ''}`}
          style={{ '--agent-color': a.color || 'var(--accent)' } as React.CSSProperties}>{(a.label || id).charAt(0).toUpperCase()}</span>)}
        {agents.length > STACK_SHOWN && <span className="ws-avatar ws-avatar-more">+{agents.length - STACK_SHOWN}</span>}
      </span>
      <span className="ws-roster-text">{state.paused ? 'Stopped' : working ? `${working} working` : 'Idle'}</span>
    </button>
    {open && <div className="ws-popover ws-roster-menu" role="dialog" aria-label="Team status">
      <ul>
        {agents.map(([id, a]) => <li key={id}>
          <span className={`ws-avatar${a.state === 'working' ? ' is-working' : ''}`} style={{ '--agent-color': a.color || 'var(--accent)' } as React.CSSProperties} aria-hidden="true">{(a.label || id).charAt(0).toUpperCase()}</span>
          <span className="ws-roster-name">{a.label || id}<small>@{a.handle || id}</small></span>
          <small className="ws-roster-state">{a.paused ? 'stopped' : a.state}{a.queued ? ` · ${a.queued} queued` : ''}</small>
        </li>)}
      </ul>
      <p className="ws-roster-mode">{mode}</p>
      <button onClick={() => { setOpen(false); onTeam(); }}>Manage team</button>
    </div>}
  </div>;
}

function StopResume({ paused, refresh }: { paused: boolean; refresh: () => void }) {
  const { open, setOpen, root } = usePopover();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const call = async (path: '/stop' | '/resume') => {
    setBusy(true); setError('');
    try { await runtime(path, {}); setOpen(false); refresh(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };
  if (paused) return <div className="ws-stop" ref={root}>
    <button className="ws-icon-button ws-resume" disabled={busy} onClick={() => void call('/resume')} aria-label="Resume all agents" title="Resume all agents">
      <Icon name="play" size={16} />
    </button>
    {error && <p className="ws-popover" role="alert">{error}</p>}
  </div>;
  return <div className="ws-stop" ref={root}>
    <button className="ws-icon-button ws-stop-button" aria-expanded={open} onClick={() => setOpen(!open)} aria-label="Stop all agents" title="Stop all agents">
      <Icon name="stop" size={16} />
    </button>
    {open && <div className="ws-popover" role="alertdialog" aria-label="Stop all agents">
      <p>Stop all agents? Active turns are cancelled and queued work is cleared. Chats and files are kept.</p>
      <div className="ws-popover-actions">
        <button className="ws-danger" disabled={busy} onClick={() => void call('/stop')}>Stop all</button>
        <button onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </div>}
  </div>;
}

export function HeaderBar({ view, projectName, onTeam }: { view: View; projectName: string; onTeam: () => void }) {
  const { data, refresh } = useRuntimeState();
  const channel = useActiveChannel();
  const left = legacy.header.querySelector<HTMLElement>('.header-left');
  const right = legacy.header.querySelector<HTMLElement>('.header-right');
  const title = view === 'chat' ? sessionTitle(channel) : VIEW_TITLES[view];
  return <>
    {left && createPortal(<div className="ws-context">
      <h1 className="ws-title">{title}</h1>
      {projectName && <span className="ws-crumb">{projectName}</span>}
      <GoalChip />
    </div>, left)}
    {right && data && createPortal(<>
      <Roster state={data} onTeam={onTeam} />
      <StopResume paused={data.paused} refresh={() => void refresh()} />
    </>, right)}
  </>;
}
