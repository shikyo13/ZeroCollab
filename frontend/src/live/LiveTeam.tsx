import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { EVENTS } from '../lib/constants';
import { useNow, since, sessionTitle } from '../lib/room';
import type { View } from '../workspace/useWorkspaceRoute';
import { useLiveTeam, type LiveSource } from './useLiveTeam';
import { useLiveBrowser } from './useLiveBrowser';
import './live.css';
import { MotionCue, useLiveCardMotion } from './MotionCue';

const timeLabel = (time: number) => new Date(time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function LiveTeam({ active, navigate }: { active: boolean; navigate: (view: View) => void }) {
  const model = useLiveTeam(active), now = useNow(1000);
  const [paused, setPaused] = useState(false);
  const [motion, setMotion] = useState<'system' | 'reduced'>('system');
  const [systemReduced, setSystemReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [pinned, setPinned] = useState<LiveSource | null>(null);
  const [focus, setFocus] = useState(false);
  const [animationAfter, setAnimationAfter] = useState(() => Date.now() / 1000);
  const focusButton = useRef<HTMLButtonElement>(null), focusHeading = useRef<HTMLHeadingElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const bubbleRoot = useRef<HTMLDivElement>(null);
  const latest = model.sources[0];
  const selected = pinned || latest;
  const reduced = motion === 'reduced' || systemReduced;
  const animate = active && model.connected && model.ready && !paused && !reduced;
  useLiveCardMotion(bubbleRoot, animate);
  const browser = useLiveBrowser(active && model.connected && model.ready && !pinned && !paused);
  const agents = Object.entries(model.room?.agents ?? {}).filter(([, a]) => !a.archived);
  const label = (id: string) => model.room?.agents[id]?.label || (id === 'user' ? 'You' : id || 'Unknown');
  const returnToFollowing = () => { setPinned(null); focusHeading.current?.focus(); };
  useEffect(() => {
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setSystemReduced(preference.matches);
    preference.addEventListener('change', change);
    return () => preference.removeEventListener('change', change);
  }, []);
  useEffect(() => { setAnimationAfter(Date.now() / 1000); }, [active, paused, reduced, model.connected, model.ready]);
  useEffect(() => {
    if (!focus || !active) return;
    const escape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); setFocus(false); focusButton.current?.focus(); }
      if (e.key === 'Tab') {
        const controls = [...(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select, a[href]') ?? [])].filter(n => n.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
        else if (!root.current?.contains(document.activeElement)) { e.preventDefault(); first?.focus(); }
      }
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [focus, active]);
  useEffect(() => { if (!active) setFocus(false); }, [active]);
  // Project switches leave no pinned evidence attributed to the new project.
  useEffect(() => { setPinned(null); }, [model.room?.project_id]);
  const openSource = (source: LiveSource) => {
    if (source.task) { navigate('board'); window.dispatchEvent(new CustomEvent(EVENTS.openTask, { detail: { task: source.task } })); }
    else if (source.channel) { window.switchChannel?.(source.channel); navigate('chat'); }
  };
  const select = (source: LiveSource) => setPinned({ ...source });
  const running = model.room?.runs.length ?? 0;
  const bubbles = model.sources.slice(0, 3);
  const stateLabel = !model.connected ? 'Connection interrupted' : !model.ready ? 'Syncing current state' : running ? `${running} running ${running === 1 ? 'turn' : 'turns'}` : 'Between actions';
  const focusLabel = pinned ? 'Pinned evidence' : !model.connected ? 'Last received' : 'Following latest';

  return <div ref={root} role={focus ? 'dialog' : undefined} aria-modal={focus || undefined} aria-label={focus ? 'Live view focus mode' : undefined} className={`live-team${focus ? ' lt-focus' : ''}${animate ? ' lt-motion' : ''}`}>
    <div className="lt-heading">
      <div><span className="workspace-eyebrow">A SHARED VIEW OF THE WORK</span><h1>Live view</h1><p>Replies, handoffs and finished artifacts, as they happen.</p></div>
      <div className="lt-controls">
        <button aria-pressed={paused} onClick={() => { setAnimationAfter(Date.now() / 1000); setPaused(v => !v); }}>{paused ? 'Resume motion' : 'Pause motion'}</button>
        <label className="lt-motion-control">Motion <select aria-label="Motion preference" value={motion} onChange={e => { setAnimationAfter(Date.now() / 1000); setMotion(e.target.value as typeof motion); }}><option value="system">System preference</option><option value="reduced">Reduced motion</option></select></label>
        <button ref={focusButton} aria-pressed={focus} onClick={() => setFocus(v => !v)}>{focus ? 'Exit focus view' : 'Focus view'}</button>
      </div>
    </div>
    <div className="lt-status"><span className={`lt-dot${model.connected && model.ready ? ' lt-connected' : ''}`} aria-hidden="true" /><span role="status">{stateLabel}{paused && ' · Motion paused · agents continue'}{reduced && ' · Reduced motion'}{model.room?.paused && ' · Agent work stopped'}</span><span>{model.received ? `Last received ${timeLabel(model.received)}` : 'Waiting for a current snapshot'}</span></div>
    {(!model.connected || model.error) && <div className="lt-connection"><p>{model.error || 'Current activity is unknown. Last received evidence stays available.'}</p><button onClick={model.refresh}>Reconnect view</button></div>}
    <div className="lt-grid">
      <section className="lt-panel lt-cast" aria-labelledby="lt-cast-heading">
        <h2 id="lt-cast-heading">The team <span>{agents.length}</span></h2>
        {agents.map(([id, agent]) => {
          const run = model.room?.runs.find(r => r.agent === id);
          const tasks = model.project?.tasks.filter(t => t.owner === id && t.status !== 'done') ?? [];
          const current = tasks.find(t => t.execution_state === 'executing');
          const waiting = tasks.find(t => ['waiting_on_dependency', 'waiting_on_human', 'blocked', 'in_review'].includes(t.execution_state || ''));
          const activity = model.sources.find(s => s.run === run?.id && run);
          const unavailable = agent.state === 'blocked' || agent.state === 'login_required';
          const state = !model.connected || !model.ready ? 'Last received · state unknown' : run ? activity?.title || 'Running a turn' : agent.paused ? 'Stopped' : unavailable ? agent.state === 'login_required' ? 'Sign-in needed' : 'Unavailable' : agent.queued ? `${agent.queued} queued` : waiting ? waiting.execution_state?.replaceAll('_', ' ') : 'Between actions';
          return <div key={id} className={`lt-agent${run && animate ? ' lt-running' : ''}`} style={{ '--lt-agent': agent.color || 'var(--accent)' } as CSSProperties}>
            <span className="lt-avatar" aria-hidden="true">{agent.label.slice(0, 1)}</span><div><strong>{agent.label}</strong><p>{state}</p>{unavailable && agent.detail && <small>{agent.detail}</small>}{(current || waiting) && <button className="lt-task-link" onClick={() => openSource({ task: (current || waiting)!.id } as LiveSource)}>{(current || waiting)!.title}</button>}{run?.channel && <small>{sessionTitle(run.channel)}</small>}</div>
          </div>;
        })}
        {!agents.length && <p className="lt-empty">No team snapshot yet. Activity will appear when the room connects.</p>}
      </section>
      <section className="lt-panel lt-stage" aria-labelledby="lt-stage-heading">
        <div className="lt-panel-head"><h2 id="lt-stage-heading">Collaboration</h2><span>{stateLabel}</span></div>
        <div className="lt-bubbles" ref={bubbleRoot}>
          {bubbles.map(source => <button key={source.id} data-live-source={source.id} className={`lt-bubble${source.fresh && source.time > animationAfter && now - source.time < 8 ? ' lt-new' : ''}${selected?.id === source.id ? ' lt-selected' : ''}`} onClick={() => select(source)} style={{ '--lt-agent': model.room?.agents[source.actor]?.color || 'var(--accent)' } as CSSProperties}>
            <small><i aria-hidden="true" />{label(source.actor)} · {source.kind}</small>
            {source.to && <div className="lt-handoff"><strong>{label(source.actor)}</strong><span className="lt-transfer" aria-hidden="true">→</span><strong>{label(source.to)}</strong></div>}
            <strong>{source.title}</strong>
            {source.motion && <MotionCue cue={source.motion} cueKey={`${source.id}:${source.motion.event}:${source.motion.state}`} animate={animate && source.fresh && source.time > animationAfter && now - source.time < 8} />}
            <p>{source.text}</p><span className="lt-source-meta">{timeLabel(source.time)} · {source.task ? `Task ${source.task}` : source.channel ? sessionTitle(source.channel) : source.kind}{source.count > 1 ? ` · ${source.count} updates` : ''}</span>
          </button>)}
          {!bubbles.length && <div className="lt-quiet"><span aria-hidden="true">◇</span><h3>{model.ready ? 'Room for the next action' : 'Connecting to the team'}</h3><p>Current agent states appear on the left. New replies and handoffs will enter here.</p></div>}
        </div>
        <div className="lt-stage-foot">{model.sources.length > 3 ? `${model.sources.length - 3} more sources in the trail` : 'Motion follows attributed events'} · Select a bubble to pin its evidence.</div>
      </section>
      <section className="lt-panel lt-focus-panel" aria-labelledby="lt-focus-heading">
        <div className="lt-panel-head"><h2 id="lt-focus-heading" ref={focusHeading} tabIndex={-1}>Shared focus</h2><span>{focusLabel}</span></div>
        {pinned && <button className="lt-return" onClick={returnToFollowing}>Return to following</button>}
        <div className="lt-preview">
          {selected?.image && (pinned || !browser.live) ? <><img src={selected.image} alt={`Finished artifact shared by ${label(selected.actor)}: ${selected.title}`} /><p>{label(selected.actor)} · {selected.kind} · {timeLabel(selected.time)}<br />Record time; image capture time may differ.</p></>
            : browser.frame ? <><img src={browser.frame} alt={browser.live ? 'Live shared browser page' : 'Last captured shared browser page'} /><p>{browser.live ? `Live shared browser · ${browser.capture?.ownerLabel || 'No controller'}` : 'Last captured browser frame'}<br />{timeLabel(browser.captured)}{browser.capture?.title ? ` · ${browser.capture.title}` : ''}</p></>
            : <div className="lt-browser-empty"><strong>{browser.state?.running ? 'Waiting for browser frame' : 'Shared browser available here'}</strong><p>When the team opens a preview, its page becomes the shared focus. Finished images can be selected from the trail.</p></div>}
          {browser.error && <><p className="lt-error">{browser.error}</p><button onClick={browser.retry}>Retry browser view</button></>}
          <button className="lt-task-link" onClick={() => { setFocus(false); window.dispatchEvent(new CustomEvent('workspace-side-panel', { detail: { tab: 'browser' } })); }}>Open Browser panel ↗</button>
        </div>
        <h3 className="lt-trail-title">Source trail <span>{model.sources.length} / 40</span></h3>
        <div className="lt-trail">{model.sources.map(source => <button key={source.id} className={selected?.id === source.id ? 'lt-selected' : ''} onClick={() => select(source)}><small>{label(source.actor)} · {source.kind} · {since(source.time, now)}</small><strong>{source.title}</strong>{source.revision && <span>Revision {source.revision}</span>}</button>)}</div>
      </section>
    </div>
    <section className="lt-panel lt-evidence" aria-labelledby="lt-evidence-heading"><div className="lt-panel-head"><h2 id="lt-evidence-heading">Selected source</h2><span>{pinned ? 'Pinned · auto focus paused' : 'Automatic focus'}</span></div>{selected ? <div className="lt-evidence-body"><small>{label(selected.actor)} · {selected.kind}{selected.ref ? ` #${selected.ref}` : ''} · {timeLabel(selected.time)}{selected.run ? ` · Run ${selected.run}` : ''}</small><h3>{selected.title}</h3>{selected.revision && <p className="lt-revision">Exact revision: {selected.revision}</p>}<p className="lt-source-text">{selected.text}</p>{(selected.task || selected.channel) && <button onClick={() => openSource(selected)}>Open {selected.task ? `task ${selected.task}` : sessionTitle(selected.channel!)} ↗</button>}</div> : <p className="lt-empty">Select a source when activity arrives.</p>}</section>
  </div>;
}
