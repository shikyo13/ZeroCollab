/* Follow mode F2: a picture-in-picture of the latest finished artifact while following.
 * - An annotation (room_markup) shows the completed image with its numbered legend. Markup is
 *   atomic, so this is the finished result, never a live drawing.
 * - An image an agent shares or attaches shows as is.
 * - An agent acting in the shared browser shows the preview page as it is now, refreshed while open.
 * Everything comes from events the room already emits (followStore.ts). */
import { useEffect, useRef, useState } from 'react';
import { sessionHeaders } from '../lib/api';
import type { Task } from '../lib/types';
import { imageNotes } from '../annotate/ImageViewer';
import { displayName, type Agents } from '../board/shared';
import { closePip, useFollow, type PipItem } from './followStore';
import './pip.css';

const HIDE_AFTER_MS = 30000;
const FRAME_REFRESH_MS = 1500;

function headline(item: PipItem): string {
  if (item.kind === 'markup') return 'annotated an image';
  if (item.kind === 'image') return 'shared an image';
  return item.action || 'is using the shared browser';
}

/** The shared browser's current frame, fetched with the session token like the Browser panel does.
 * Fetches only while active() is true (Follow running, card on screen, page visible); otherwise the
 * last frame stays frozen and no request is made. A new target starts blank, and anything that
 * arrives after the target changed or the card closed is discarded, never shown under the wrong caption. */
function useBrowserFrame(item: PipItem | null, paused: boolean, active: () => boolean) {
  const [frame, setFrame] = useState('');
  const activeRef = useRef(active);
  activeRef.current = active;
  const request = useRef<AbortController | null>(null);
  // Pausing keeps the same target, so the effect below does not rerun: abort the request in flight here.
  useEffect(() => { if (paused) request.current?.abort(); }, [paused]);
  useEffect(() => {
    setFrame('');
    if (!item || item.kind !== 'browser' || !item.project) return;
    let stopped = false;
    let objectUrl = '';
    let timer = 0;
    // A body that lands after Follow paused or the card went off screen must not change the frozen frame.
    const current = () => !stopped && activeRef.current();
    const load = async () => {
      if (activeRef.current()) {
        const controller = request.current = new AbortController();
        try {
          const response = await fetch('/api/runtime/browser/frame?project=' + encodeURIComponent(item.project!),
            { headers: sessionHeaders(), signal: controller.signal });
          if (response.ok && current()) {
            const body = await response.blob();
            if (current()) {  // the target may have changed, or Follow paused, while the body downloaded
              const fresh = URL.createObjectURL(body);
              if (objectUrl) URL.revokeObjectURL(objectUrl);
              objectUrl = fresh;
              setFrame(fresh);
            }
          }
        } catch { /* aborted, or the browser closed; keep the last frame */ }
      }
      if (!stopped) timer = window.setTimeout(load, FRAME_REFRESH_MS);
    };
    void load();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      request.current?.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item?.id, item?.kind, item?.project]);
  return frame;
}

export function FollowPip({ agents, tasks, onOpenTask }: { agents: Agents; tasks: Task[]; onOpenTask: (id: string) => void }) {
  const snap = useFollow();
  // Pinned keeps the current artifact on screen while newer ones arrive.
  const [pinned, setPinned] = useState<PipItem | null>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const root = useRef<HTMLElement>(null);
  const item = snap.following ? pinned ?? snap.pip : null;
  // Paused freezes the frame like the rest of Follow; a hidden Board or background tab makes no requests.
  // The card is position: fixed, so offsetParent is always null; a hidden ancestor leaves it without boxes.
  const frame = useBrowserFrame(item, snap.paused, () =>!snap.paused && !document.hidden && !!root.current?.getClientRects().length);

  // A newer artifact hides itself after a while unless paused, pinned, hovered or focused. The timer
  // re-arms when focus or the pointer leaves, so a card that was focused when it expired still goes.
  useEffect(() => {
    if (!item || snap.paused || pinned || hovered || focused) return;
    const timer = window.setTimeout(() => closePip(), HIDE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [item?.id, snap.paused, pinned, hovered, focused]);
  useEffect(() => { if (!snap.following) setPinned(null); }, [snap.following]);

  const close = () => {
    const hadFocus = !!root.current?.contains(document.activeElement);
    setPinned(null);
    closePip();
    // The card unmounts; keep keyboard users on Follow: the visible toggle, else the banner.
    if (hadFocus) [...document.querySelectorAll<HTMLElement>('.fl-toggle, .fl-banner')].find(el => el.offsetParent)?.focus();
  };

  if (!item) return null;
  const notes = item.kind === 'markup' ? imageNotes(item.note) : null;
  const task = item.task ? tasks.find(t => t.id === item.task) : undefined;
  const time = new Date(item.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const color = agents[item.actor]?.color || 'var(--accent)';
  return (
    <aside ref={root} className="fl-pip" aria-label="Latest team artifact" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}>
      <header className="fl-pip-head">
        <span className="fl-pip-avatar" style={{ background: color }} aria-hidden="true">{displayName(item.actor, agents).slice(0, 2).toUpperCase()}</span>
        <p><strong>{displayName(item.actor, agents)}</strong> {headline(item)} · {time}</p>
        <button type="button" className="fl-pip-btn" aria-pressed={!!pinned} title={pinned ? 'Unpin: show newer artifacts' : 'Pin: keep this one on screen'}
          onClick={() => setPinned(pinned ? null : item)}>{pinned ? 'Unpin' : 'Pin'}</button>
        <button type="button" className="fl-pip-btn" aria-label="Close" title="Close until the next artifact"
          onClick={close}>✕</button>
      </header>
      {item.kind === 'browser' ? (
        <figure className="fl-pip-figure">
          {frame ? <img src={frame} alt="The shared browser preview as it is now" /> : <p className="fl-pip-empty">Waiting for the shared browser…</p>}
          <figcaption>Shared browser now{item.url ? ` · ${item.url.replace(/^https?:\/\/(127\.0\.0\.1|localhost)/, '')}` : ''}</figcaption>
        </figure>
      ) : (
        <figure className="fl-pip-figure">
          <a href={item.url} target="_blank" rel="noreferrer" title="Open the full image"><img src={item.url} alt={notes?.summary || 'Shared image'} /></a>
          {notes && notes.marks.length > 0 && (
            <ol className="fl-pip-legend">
              {notes.marks.map(mark => (
                <li key={mark.number}><span className="fl-pip-key" style={{ background: mark.color || 'var(--accent)' }}>{mark.number}</span>{mark.text}</li>
              ))}
            </ol>
          )}
          <figcaption>
            {item.kind === 'markup' ? 'Finished annotation, shown once it was saved' : 'Shared image'}
            {task && <> · <button type="button" className="fl-pip-link" onClick={() => onOpenTask(task.id)}>{task.title}</button></>}
          </figcaption>
        </figure>
      )}
    </aside>
  );
}
