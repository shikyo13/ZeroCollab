/* The watch session beside the stage (task b42437b9, design contract 9f2c917a).
 * Follow this work: one task's current people, conversation and evidence, kept open through handoffs.
 * Follow person: one agent's state, conversation and latest evidence. Evidence is real pixels with
 * provenance; a pinned picture stays as chosen while newer ones arrive. */
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { EVENTS } from '../lib/constants';
import type { View } from '../workspace/useWorkspaceRoute';
import { isVideo, type LiveAgent, type LiveArtifact, type LiveFeed, type LiveInteraction } from './liveFeed';
import { stateWords } from './LiveStage';
import { useLiveBrowser } from './useLiveBrowser';
import { MediaAttachment } from '../components/MediaAttachment';

type Shown = { owner: string; artifact: LiveArtifact };

export type Focus = { kind: 'work'; task: string } | { kind: 'person'; agent: string } | null;

const WORDS: Record<LiveInteraction['kind'], string> = {
  mention: 'mentioned', handoff: 'handed over to', review_request: 'asked for a review from', review_verdict: 'reviewed for',
  followup: 'asked', followup_result: 'answered', decision_turn: 'took a decision turn with', question: 'asked you', answer: 'answered',
};
const KIND: Record<LiveArtifact['kind'], string> = { screenshot: 'Screenshot', markup: 'Annotation', upload: 'Upload', browser_frame: 'Shared browser' };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export function LiveSession({ feed, focus, paused, navigate, onClose, onFocus }: {
  feed: LiveFeed; focus: Focus; paused: boolean; navigate: (view: View) => void; onClose: () => void; onFocus: (focus: Focus) => void;
}) {
  const [pinned, setPinned] = useState<Shown | null>(null);
  const [marked, setMarked] = useState(true);
  // Reading protection (design 3e3c7401 F1): while the pointer or keyboard focus is in the session, what is on
  // screen stays exactly as it is; newer evidence waits behind 'Newer picture'. Pin keeps it after you leave.
  const [pointerIn, setPointerIn] = useState(false), [focusIn, setFocusIn] = useState(false);
  const reading = pointerIn || focusIn;
  const [held, setHeld] = useState<Shown | null>(null);
  const last = useRef<Shown | null>(null);
  const key = focus ? (focus.kind === 'work' ? `w:${focus.task}` : `p:${focus.agent}`) : '';
  useEffect(() => { setPinned(null); setHeld(null); setMarked(true); last.current = null; }, [key]);
  useEffect(() => { setHeld(reading ? last.current : null); }, [reading]);

  const name = (id: string) => id === 'user' ? 'You' : id === 'team' ? 'everyone' : id === 'room' ? 'the room' : id === 'unassigned' ? 'nobody' : feed.agents.find(a => a.id === id)?.short || id;
  const people: LiveAgent[] = !focus ? [] : focus.kind === 'person'
    ? feed.agents.filter(a => a.id === focus.agent)
    // The work's people: whoever is on the task, plus both sides of a pending review or open follow-up about it.
    : feed.agents.filter(a => a.task_id === focus.task || feed.links.some(l => l.task_id === focus.task && (l.from === a.id || l.to === a.id)));
  // Work: anything tied to the task, plus anything untied said between the people on it right now. A reviewer
  // or follow-up partner may be busy elsewhere, so their exchanges about another task stay out (design 14b0b5b1).
  const members = new Set(people.map(p => p.id));
  const talk = !focus ? [] : feed.interactions.filter(i => focus.kind === 'work'
    ? i.task_id === focus.task || (!i.task_id && members.has(i.from) && members.has(i.to))
    : i.from === focus.agent || i.to === focus.agent).slice(0, 30);
  // Evidence: pinned, else held while reading, else the newest among the people in focus. When nobody is on the
  // work any more (a handoff, a finished turn), the last picture shown stays rather than vanishing.
  // On work, a reviewer or follow-up partner may be busy with other work: only pictures from this task count.
  const onWork = (p: LiveAgent) => !focus || focus.kind === 'person' || (p.artifact?.task_id ? p.artifact.task_id === focus.task : p.task_id === focus.task);
  const candidates = people.filter(p => p.artifact && onWork(p)).sort((p, q) => q.artifact!.at - p.artifact!.at);
  const newest: Shown | null = candidates[0] ? { owner: candidates[0].id, artifact: candidates[0].artifact! } : null;
  const shown = pinned ?? held ?? newest ?? last.current;
  if (shown) last.current = shown;
  const kept = pinned ?? held;
  const newer = !!kept && !!newest && (newest.artifact.url !== kept.artifact.url || newest.artifact.at !== kept.artifact.at);
  const isBrowser = shown?.artifact.kind === 'browser_frame';
  // Browser frames are bound to the chosen artifact's own page (F2); a pinned browser pane freezes its frame.
  const browser = useLiveBrowser(!!shown && isBrowser && !paused && !pinned, isBrowser ? shown!.artifact.url : undefined);
  const title = !focus ? '' : focus.kind === 'person' ? name(focus.agent)
    // The title is the focused task's own, never a member's other current work (design 14b0b5b1).
    : feed.agents.find(a => a.task_id === focus.task && a.task_title)?.task_title || feed.links.find(l => l.task_id === focus.task && l.task_title)?.task_title
      || talk.find(i => i.task_id === focus.task && i.task_title)?.task_title || 'This work';
  const openTask = (task: string) => { navigate('board'); window.dispatchEvent(new CustomEvent(EVENTS.openTask, { detail: { task } })); };

  if (!focus) return <aside className="lv-session lv-session-empty" aria-label="Watch session">
    <h2>Watch something</h2>
    <p>Choose a <b>task chip</b> to follow that work through handoffs, or a <b>person</b> to follow them. Their conversation and real screenshots, annotations and browser frames appear here.</p>
  </aside>;

  const src = shown && (shown.artifact.kind === 'markup' && !marked && shown.artifact.source_url ? shown.artifact.source_url : shown.artifact.url);
  return <aside className="lv-session" aria-label={`Watch session: ${title}`}
    onPointerEnter={() => setPointerIn(true)} onPointerLeave={() => setPointerIn(false)}
    onFocus={() => setFocusIn(true)} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocusIn(false); }}>
    <div className="lv-session-head">
      <div>
        <small>{focus.kind === 'work' ? 'FOLLOWING THIS WORK' : 'FOLLOWING'}</small>
        <h2>{title}</h2>
        {focus.kind === 'person' && feed.agents.find(a => a.id === focus.agent)?.name !== title && <p className="lv-role">{feed.agents.find(a => a.id === focus.agent)?.name}</p>}
      </div>
      <button type="button" onClick={onClose} aria-label="Close watch session">✕</button>
    </div>
    {focus.kind === 'work' && <button type="button" className="lv-link" onClick={() => openTask(focus.task)}>Open on the Board ↗</button>}
    {focus.kind === 'person' && people[0]?.task_id && <button type="button" className="lv-link" onClick={() => onFocus({ kind: 'work', task: people[0].task_id! })}>Follow their work: {people[0].task_title || 'current task'}</button>}

    <ul className="lv-people" aria-label="People">
      {people.length ? people.map(p => <li key={p.id} style={{ '--c': p.color } as CSSProperties}>
        <button type="button" onClick={() => onFocus({ kind: 'person', agent: p.id })}><i aria-hidden="true" />{p.short}</button>
        <span className={`lv-state lv-${p.state}`}>{stateWords(p)}</span>
      </li>) : <li className="lv-muted">Nobody is on this right now. The conversation below is kept.</li>}
    </ul>

    <section className="lv-evidence" aria-label="Evidence">
      {shown ? <>
        <div className="lv-evidence-pic">
          {isBrowser
            ? browser.frame ? <img src={browser.frame} alt={`Shared browser, driven by ${name(shown.owner)}`} /> : <p className="lv-muted">{browser.error || 'Waiting for the shared browser frame.'}</p>
            : isVideo(src!)
              // Videos (rendered demos, recordings): native controls, never autoplay, no sound until asked.
              ? <MediaAttachment url={src!} name={`Video from ${name(shown.owner)}`} className="lv-media" paused={paused} />
              : <img src={src!} alt={`${KIND[shown.artifact.kind]} from ${name(shown.owner)}`} />}
        </div>
        <p className="lv-provenance">
          {name(shown.owner)} · {KIND[shown.artifact.kind]}{isBrowser ? (browser.live ? ' · live' : ' · last frame') : ''} · {clock(isBrowser && browser.captured ? browser.captured : shown.artifact.at)}
          {shown.artifact.page ? ` · page ${shown.artifact.page}` : ''}
        </p>
        <div className="lv-evidence-actions">
          {shown.artifact.kind === 'markup' && shown.artifact.source_url && <div role="group" aria-label="Annotation view" className="lv-toggle">
            <button type="button" aria-pressed={!marked} onClick={() => setMarked(false)}>Original</button>
            <button type="button" aria-pressed={marked} onClick={() => setMarked(true)}>Marked</button>
          </div>}
          <button type="button" aria-pressed={!!pinned} onClick={() => setPinned(pinned ? null : shown)} title={isBrowser ? 'Keep this frame; the browser keeps working' : 'Keep this picture as newer ones arrive'}>{pinned ? 'Pinned' : 'Pin'}</button>
          {!isBrowser && !isVideo(src!) && window.ZeroImageReview && <button type="button" onClick={() => window.ZeroImageReview?.([{ url: src!, name: KIND[shown.artifact.kind], note: shown.artifact.note }], 0, () => undefined)}>Expand</button>}
          {newer && <button type="button" className="lv-new" onClick={() => { setPinned(null); setHeld(reading ? newest : null); }}>Newer picture ›</button>}
        </div>
        {shown.artifact.note && marked && <pre className="lv-note">{shown.artifact.note}</pre>}
      </> : <p className="lv-muted">No screenshot, annotation or browser frame yet. When one is made, it appears here.</p>}
    </section>

    <section className="lv-talk" aria-label="Conversation">
      <h3>Conversation</h3>
      {people.filter(p => p.speech && (focus.kind === 'person' || p.task_id === focus.task)).map(p => <p key={p.id} className="lv-speech" style={{ '--c': p.color } as CSSProperties}><b>{p.short}</b> {p.speech}</p>)}
      <ol>
        {talk.map(i => <li key={i.id}>
          <small>{clock(i.at)}</small> <b>{name(i.from)}</b> {WORDS[i.kind]} <b>{name(i.kind === 'handoff' ? i.to_owner || i.to : i.to)}</b>
          {i.text && i.kind !== 'handoff' && <span className="lv-quote">{i.text}</span>}
        </li>)}
        {!talk.length && <li className="lv-muted">No mentions, handoffs or reviews yet.</li>}
      </ol>
    </section>
  </aside>;
}
