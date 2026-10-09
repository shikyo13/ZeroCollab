import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { TaskAttachment } from '../lib/types';
import './viewer.css';

/** Older notes have no color metadata. Keep those neutral rather than guessing. */
export function imageNotes(note = '') {
  const parts = note.split(/(?=#\d+\s+(?:box|arrow|pin)\b)/i);
  let summary = '';
  const marks: { number: string; kind: string; text: string; color?: string }[] = [];
  for (const part of parts) {
    const match = part.trim().match(/^#(\d+)\s+(box|arrow|pin)\b[^:]*:\s*([\s\S]*)$/i);
    if (!match) { summary += part; continue; }
    const color = match[3].match(/\s*\[color=(#[0-9a-f]{6})\]\s*$/i)?.[1];
    marks.push({ number: match[1], kind: match[2], text: match[3].replace(/\s*\[color=#[0-9a-f]{6}\]\s*$/i, '').trim(), color });
  }
  return { summary: summary.trim(), marks };
}

export function uploadSource(value?: string) {
  if (!value) return undefined;
  try {
    const url = new URL(value, location.href);
    return url.origin === location.origin && /^\/uploads\/[a-z0-9_-][a-z0-9_.-]*\.(png|jpe?g|gif|webp)$/i.test(url.pathname) ? url.pathname : undefined;
  } catch { return undefined; }
}

export function ImageViewer({ images, initial = 0, onClose, onAnnotate }: {
  images: TaskAttachment[]; initial?: number; onClose: () => void;
  onAnnotate?: (attachment: TaskAttachment) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setIndex] = useState(initial);
  const index = Math.max(0, Math.min(selected, images.length - 1));
  const [original, setOriginal] = useState(false);
  const [actual, setActual] = useState(false);
  const [failed, setFailed] = useState(false);
  const attachment = images[index];
  const source = uploadSource(attachment.source);
  const notes = imageNotes(attachment.note);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => { setOriginal(false); setFailed(false); }, [index]);
  const change = (delta: number) => setIndex(i => Math.max(0, Math.min(images.length - 1, i + delta)));
  return <dialog ref={dialog} className="iv-dialog" aria-label="Image review" onClose={onClose}
    onKeyDown={event => {
      event.stopPropagation();
      if (event.key === 'ArrowLeft') { event.preventDefault(); change(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); change(1); }
    }}>
    <header className="iv-header">
      <h2>{attachment.name || 'Image review'}</h2>
      {images.length > 1 && <div className="iv-controls"><button onClick={() => change(-1)} disabled={!index} aria-label="Previous image">←</button><span>{index + 1} / {images.length}</span><button onClick={() => change(1)} disabled={index === images.length - 1} aria-label="Next image">→</button></div>}
      <button onClick={() => setActual(v => !v)} aria-pressed={actual}>{actual ? 'Fit image' : 'Actual size'}</button>
      <button onClick={() => dialog.current?.close()} aria-label="Close image review">Close ×</button>
    </header>
    <div className="iv-body">
      <div className={`iv-stage${actual ? ' iv-actual' : ''}`}>
        <img key={`${index}:${original}`} src={original && source ? source : attachment.url} alt={attachment.name || 'Shared image'} onLoad={() => setFailed(false)} onError={() => setFailed(true)} />
        {failed && <p role="alert">This image could not be loaded.{original ? ' Switch back to Annotated to view the saved copy.' : ''}</p>}
      </div>
      <aside className="iv-notes" aria-label="Annotation notes">
        <h3>Annotation notes</h3>
        {source && <div className="iv-controls" aria-label="Image version"><button aria-pressed={!original} onClick={() => setOriginal(false)}>Annotated</button><button aria-pressed={original} onClick={() => setOriginal(true)}>Original</button></div>}
        {notes.summary && <p className="iv-summary">{notes.summary}</p>}
        {notes.marks.length > 0 ? <ol className="iv-mark-list">{notes.marks.map((mark, i) => <li key={i} style={{ '--mark-color': mark.color || 'var(--text-dim)' } as CSSProperties}>
          <h4><span className="iv-number">{mark.number}</span><span>{mark.kind}</span></h4><p>{mark.text || 'No note for this mark.'}</p>
        </li>)}</ol> : !notes.summary && <p className="iv-muted">No annotation notes for this image.</p>}
        {notes.marks.some(mark => !mark.color) && <p className="iv-muted">Colors were not saved with these older notes. Match the numbers on the image.</p>}
        {onAnnotate && uploadSource(attachment.url) && <div className="iv-annotate">
          <p className="iv-muted">{source ? 'Start a new review on the saved original. This marked-up copy stays unchanged.' : notes.marks.length ? 'No separate original is recorded. Existing marks in these pixels cannot be removed.' : 'Save a separate annotated copy. This image stays unchanged.'}</p>
          <button onClick={() => {
            if (!source && notes.marks.length && !window.confirm('No original is recorded. New marks will be drawn over this image. Continue?')) return;
            dialog.current?.close(); onAnnotate({ ...attachment, url: source || attachment.url, source: undefined });
          }}>{source ? 'Annotate original' : 'Annotate this image'}</button>
        </div>}
      </aside>
    </div>
  </dialog>;
}
