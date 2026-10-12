/* Live view v2 (task b42437b9, Zero #14152). The Board is about the work; Live is about the team right
 * now: who is working with whom, handoffs and mentions as motion, questions for you rising to the You
 * area, and each person's latest real evidence. A task appears only as a chip on the group working on
 * it; the chip opens a watch session, and from there the same card on the Board. */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { errorMessage, runtime } from '../lib/api';
import type { View } from '../workspace/useWorkspaceRoute';
import { useLiveFeed, type LiveApproval } from './liveFeed';
import { LiveStage } from './LiveStage';
import { LiveSession, type Focus } from './LiveSession';
import './live.css';

const YOU = { x: 12, y: 12, w: 300, h: 64 };

/** Answer a question or decide an approval right where it surfaced, through the room's approval route
 * (the same one the chat's approval card uses). The item leaves the You area once the room resolves it. */
function YouAnswer({ ask, who, onChat, onDone }: { ask: LiveApproval; who: string; onChat: () => void; onDone: () => void }) {
  const [answer, setAnswer] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { field.current?.focus(); }, []);
  const decide = async (allow: boolean) => {
    setSending(true); setError('');
    try { await runtime(`/approval/${encodeURIComponent(ask.id)}`, { allow, answer: ask.question ? answer.trim() : '' }); onDone(); }
    catch (e) { setError(errorMessage(e)); setSending(false); }
  };
  return <div className="lv-answer" role="group" aria-label={`${ask.question ? 'Answer' : 'Decide'} for ${who}`}
    onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onDone(); } }}>
    <p>{ask.text}</p>
    {ask.question && <textarea ref={field} rows={2} value={answer} onChange={e => setAnswer(e.target.value)} placeholder="Your answer"
      onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && answer.trim()) void decide(true); }} />}
    <div className="lv-answer-actions">
      {ask.question
        ? <button type="button" className="lv-primary" disabled={sending || !answer.trim()} onClick={() => void decide(true)}>Send answer</button>
        : <button type="button" className="lv-primary" disabled={sending} onClick={() => void decide(true)}>Allow once</button>}
      <button type="button" disabled={sending} onClick={() => void decide(false)}>{ask.question ? 'Decline' : 'Deny'}</button>
      <button type="button" className="lv-link" onClick={onChat}>Open chat ↗</button>
    </div>
    {error && <p className="lv-answer-error" role="alert">{error}</p>}
  </div>;
}

export default function LiveTeam({ active, navigate }: { active: boolean; navigate: (view: View) => void }) {
  const feed = useLiveFeed(active);
  // The You area's real height (it grows when you open a question), so the stage keeps people clear of it.
  const youRef = useRef<HTMLElement>(null);
  const [youH, setYouH] = useState(YOU.h);
  useEffect(() => {
    const el = youRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setYouH(Math.max(YOU.h, Math.ceil(el.offsetHeight))));
    ro.observe(el); return () => ro.disconnect();
  }, [active]);
  const youArea = useMemo(() => ({ ...YOU, h: youH }), [youH]);
  const [paused, setPaused] = useState(false);
  // Pause holds the displayed stage exactly: it shows the snapshot taken when you paused. The You area and
  // the watch session stay live, so anything waiting on you can still be answered.
  const frozen = useRef(feed);
  if (!paused) frozen.current = feed;
  const stageFeed = paused ? frozen.current : feed;
  const [motion, setMotion] = useState<'system' | 'reduced'>('system');
  const [systemReduced, setSystemReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [focus, setFocus] = useState<Focus>(null);
  const [legend, setLegend] = useState(false);
  const reduced = motion === 'reduced' || systemReduced;

  useEffect(() => {
    const q = matchMedia('(prefers-reduced-motion: reduce)'), change = () => setSystemReduced(q.matches);
    q.addEventListener('change', change); return () => q.removeEventListener('change', change);
  }, []);
  // Space pauses, Escape closes the watch session; never while typing.
  useEffect(() => {
    if (!active) return;
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.code === 'Space' && !(t && t.tagName === 'BUTTON')) { e.preventDefault(); setPaused(p => !p); }
      if (e.key === 'Escape') { if (legend) setLegend(false); else setFocus(null); }
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [active, legend]);
  useEffect(() => { setFocus(null); }, [feed.project]);

  // What is waiting on you: the room's live approvals (questions to answer, tools to allow).
  const asks = feed.approvals;
  const [openAsk, setOpenAsk] = useState<string | null>(null);
  useEffect(() => { if (openAsk && !asks.some(a => a.id === openAsk)) setOpenAsk(null); }, [asks, openAsk]);
  const agent = (id: string) => feed.agents.find(a => a.id === id);
  const working = feed.agents.filter(a => a.state === 'working').length;
  const status = !feed.connected ? 'Reconnecting… showing the last known state' : !feed.ready ? 'Catching up with the team…'
    : `${working} working · ${asks.length ? `${asks.length} waiting on you` : 'nothing waiting on you'}${paused ? ' · paused' : ''}${reduced ? ' · reduced motion' : ''}`;

  return <div className={`live-v2${focus ? ' lv-with-session' : ''}${reduced ? ' lv-reduced' : ''}`}>
    <header className="lv-head">
      <div className="workspace-page-heading"><h1>Live</h1><p role="status">{status}</p></div>
      <div className="lv-controls">
        <button type="button" aria-pressed={paused} onClick={() => setPaused(p => !p)} title="Space">{paused ? '▶ Resume' : '❚❚ Pause'}</button>
        <button type="button" aria-pressed={reduced} onClick={() => setMotion(m => m === 'reduced' ? 'system' : 'reduced')} disabled={systemReduced} title={systemReduced ? 'Your system asks for reduced motion' : undefined}>Reduced motion</button>
        <button type="button" aria-expanded={legend} onClick={() => setLegend(v => !v)} aria-label="What the motion means">?</button>
      </div>
      {legend && <div className="lv-legend" role="dialog" aria-label="What the motion means">
        <p><b>Gathering</b> people working on the same task · <b>Comet</b> an @mention or review request · <b>Baton</b> a task handed over · <b>Gold rise</b> a question for you · <b>Ring step</b> a tool ran · <b>Shake</b> a turn failed.</p>
        <p>Choose a task chip to follow that work through handoffs, or a person to follow them. The Board keeps every task and its status.</p>
      </div>}
    </header>
    <div className="lv-body">
      <div className="lv-stage-wrap">
        <section ref={youRef} className={`lv-you${asks.length ? ' lv-asking' : ''}`} style={{ left: YOU.x, top: YOU.y, width: YOU.w, minHeight: YOU.h } as CSSProperties} aria-label="Waiting on you">
          <small>YOU</small>
          {asks.length ? <ul>{asks.slice(0, 3).map(q => <li key={q.id} style={{ '--c': agent(q.agent)?.color } as CSSProperties}>
            <button type="button" aria-expanded={openAsk === q.id} onClick={() => setOpenAsk(o => o === q.id ? null : q.id)}>
              <b>{agent(q.agent)?.name || q.agent}</b> {q.question ? q.text : `wants to ${q.tool}`}
            </button>
            {openAsk === q.id && <YouAnswer ask={q} who={agent(q.agent)?.name || q.agent}
              onChat={() => { window.switchChannel?.(q.channel); navigate('chat'); }} onDone={() => setOpenAsk(null)} />}
          </li>)}
            {asks.length > 3 && <li className="lv-muted">and {asks.length - 3} more</li>}</ul>
            : <p>Nothing needs you right now.</p>}
        </section>
        {active && <LiveStage feed={stageFeed} reduced={reduced} paused={paused} youArea={youArea}
          selectedPerson={focus?.kind === 'person' ? focus.agent : null} selectedTask={focus?.kind === 'work' ? focus.task : null}
          onSelectPerson={id => setFocus({ kind: 'person', agent: id })} onFollowWork={task => setFocus({ kind: 'work', task })} />}
        {feed.error && <p className="lv-error" role="alert">{feed.error}</p>}
        {feed.ready && !feed.agents.length && <p className="lv-empty">No teammates in this project yet.</p>}
      </div>
      {active && focus && <LiveSession feed={feed} focus={focus} paused={paused} navigate={navigate} onClose={() => setFocus(null)} onFocus={setFocus} />}
    </div>
  </div>;
}
