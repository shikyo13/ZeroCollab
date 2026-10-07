/* Image annotation: numbered boxes, arrows and pins with a note per mark.
 * Saving flattens the marks onto a new PNG at the image's natural size, and
 * produces a text note ("#1 box: ...") so agents get the exact wording even
 * when they cannot view images. Marks use 0..1 coordinates so the on-screen
 * preview and the exported image share one drawing routine. */
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import './annotate.css';

export type MarkKind = 'box' | 'arrow' | 'pin';

export interface Mark {
  kind: MarkKind;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color: string;
  note: string;
}

export interface AnnotationResult {
  blob: Blob;
  note: string;
}

const COLORS = ['#ff4d4f', '#facc15', '#22d3ee', '#ffffff'];
const KIND_LABELS: Record<MarkKind, string> = { box: 'Box', arrow: 'Arrow', pin: 'Pin' };

/** Draw marks scaled to a canvas of width x height (device pixels). */
function drawMarks(ctx: CanvasRenderingContext2D, width: number, height: number, marks: Mark[]) {
  const line = Math.max(2, Math.round(Math.min(width, height) / 250));
  const badge = Math.max(9, Math.round(Math.min(width, height) / 45));
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  marks.forEach((m, i) => {
    const x1 = m.x1 * width;
    const y1 = m.y1 * height;
    const x2 = m.x2 * width;
    const y2 = m.y2 * height;
    ctx.strokeStyle = m.color;
    ctx.fillStyle = m.color;
    ctx.lineWidth = line;
    // A dark halo keeps marks readable on light and dark screenshots alike.
    ctx.shadowColor = 'rgba(0, 0, 0, 0.65)';
    ctx.shadowBlur = line * 1.5;
    if (m.kind === 'box') {
      ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
    } else if (m.kind === 'arrow') {
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const head = line * 5;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - head * Math.cos(angle - Math.PI / 7), y2 - head * Math.sin(angle - Math.PI / 7));
      ctx.lineTo(x2 - head * Math.cos(angle + Math.PI / 7), y2 - head * Math.sin(angle + Math.PI / 7));
      ctx.closePath();
      ctx.fill();
    }
    // Numbered badge: at the pin itself, the box's corner, or the arrow's tail.
    const bx = m.kind === 'box' ? Math.min(x1, x2) : x1;
    const by = m.kind === 'box' ? Math.min(y1, y2) : y1;
    ctx.beginPath();
    ctx.arc(bx, by, badge, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = m.color === '#ffffff' || m.color === '#facc15' || m.color === '#22d3ee' ? '#0b0d10' : '#ffffff';
    ctx.font = `700 ${Math.round(badge * 1.1)}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(i + 1), bx, by + 1);
  });
  ctx.shadowBlur = 0;
}

/** Compose the text agents read alongside the annotated image. */
export function annotationNote(summary: string, marks: Mark[]): string {
  const lines = marks.map((m, i) => `#${i + 1} ${m.kind}: ${m.note.trim() || '(no note)'}`);
  return [summary.trim(), ...lines].filter(Boolean).join('\n').slice(0, 1000);
}

export function AnnotationEditor({
  src,
  title,
  onSave,
  onClose,
}: {
  src: string;
  title: string;
  onSave: (result: AnnotationResult) => Promise<void>;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [kind, setKind] = useState<MarkKind>('box');
  const [color, setColor] = useState(COLORS[0]);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [draft, setDraftState] = useState<Mark | null>(null);
  // Pointer events can arrive faster than renders; the ref is the source of truth while drawing.
  const draftRef = useRef<Mark | null>(null);
  const setDraft = (mark: Mark | null) => {
    draftRef.current = mark;
    setDraftState(mark);
  };
  const [summary, setSummary] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  // Redraw the preview overlay at the displayed size whenever marks or layout change.
  useEffect(() => {
    const image = imageRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas || !loaded) return;
    const paint = () => {
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(image.clientWidth * ratio);
      canvas.height = Math.round(image.clientHeight * ratio);
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawMarks(ctx, canvas.width, canvas.height, draft ? [...marks, draft] : marks);
    };
    paint();
    const observer = new ResizeObserver(paint);
    observer.observe(image);
    return () => observer.disconnect();
  }, [marks, draft, loaded]);

  const point = (e: ReactPointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    };
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (busy || marks.length >= 20) return;
    const { x, y } = point(e);
    if (kind === 'pin') {
      setMarks(m => [...m, { kind, x1: x, y1: y, x2: x, y2: y, color, note: '' }]);
      return;
    }
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* capture is a convenience; drawing still works without it */
    }
    setDraft({ kind, x1: x, y1: y, x2: x, y2: y, color, note: '' });
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const current = draftRef.current;
    if (!current) return;
    const { x, y } = point(e);
    setDraft({ ...current, x2: x, y2: y });
  };
  const onPointerUp = () => {
    const current = draftRef.current;
    if (!current) return;
    // Ignore accidental clicks that would leave an invisible box or arrow.
    if (Math.hypot(current.x2 - current.x1, current.y2 - current.y1) > 0.01) setMarks(m => [...m, current]);
    setDraft(null);
  };

  const undo = () => setMarks(m => m.slice(0, -1));
  const requestClose = () => {
    if (marks.length && !busy && !window.confirm('Discard these annotations?')) return;
    dialogRef.current?.close();
  };
  const setNote = (index: number, note: string) => setMarks(m => m.map((mark, i) => (i === index ? { ...mark, note } : mark)));

  const save = async () => {
    const image = imageRef.current;
    if (!image) return;
    setBusy(true);
    setError('');
    try {
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('This browser cannot draw the annotated image');
      ctx.drawImage(image, 0, 0);
      drawMarks(ctx, canvas.width, canvas.height, marks);
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('Could not create the annotated image');
      await onSave({ blob, note: annotationNote(summary, marks) });
      dialogRef.current?.close();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      className="an-dialog"
      aria-labelledby="an-title"
      onCancel={e => {
        // Esc with marks in progress asks first instead of discarding work.
        if (marks.length && !window.confirm('Discard these annotations?')) e.preventDefault();
      }}
      onClose={onClose}
      onKeyDown={e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !(e.target as HTMLElement).closest('input, textarea')) {
          e.preventDefault();
          undo();
        }
      }}
    >
      <header className="an-head">
        <h2 id="an-title">Annotate {title}</h2>
        <div className="an-tools" role="toolbar" aria-label="Annotation tools">
          {(Object.keys(KIND_LABELS) as MarkKind[]).map(k => (
            <button key={k} type="button" className="an-btn" aria-pressed={kind === k} onClick={() => setKind(k)}>
              {KIND_LABELS[k]}
            </button>
          ))}
          <span className="an-sep" aria-hidden="true" />
          {COLORS.map(c => (
            <button
              key={c}
              type="button"
              className="an-swatch"
              style={{ background: c }}
              aria-label={`Color ${c}`}
              aria-pressed={color === c}
              onClick={() => setColor(c)}
            />
          ))}
          <span className="an-sep" aria-hidden="true" />
          <button type="button" className="an-btn" onClick={undo} disabled={!marks.length}>
            Undo
          </button>
        </div>
        <button type="button" className="an-btn an-close" onClick={requestClose} aria-label="Close without saving">
          ×
        </button>
      </header>
      <div className="an-body">
        <div className="an-stage">
          <div className="an-frame">
            <img ref={imageRef} src={src} alt={title} onLoad={() => setLoaded(true)} onError={() => setError('The image could not be loaded')} draggable={false} />
            <canvas
              ref={canvasRef}
              className="an-canvas"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={() => setDraft(null)}
              aria-label={`Drawing area: drag to add a ${kind === 'pin' ? 'pin (click)' : kind}`}
            />
          </div>
        </div>
        <aside className="an-side">
          <p className="an-hint">
            {kind === 'pin' ? 'Click to drop a numbered pin.' : `Drag to draw ${kind === 'box' ? 'a box' : 'an arrow'}.`} Each mark gets a number
            and its own note; agents receive the marked image and these notes.
          </p>
          <label className="an-field">
            <span>Summary</span>
            <textarea value={summary} onChange={e => setSummary(e.target.value)} rows={2} placeholder="What should change?" />
          </label>
          {marks.length > 0 ? (
            <ol className="an-marks">
              {marks.map((m, i) => (
                <li key={i}>
                  <span className="an-badge" style={{ background: m.color }}>
                    {i + 1}
                  </span>
                  <input
                    value={m.note}
                    onChange={e => setNote(i, e.target.value)}
                    placeholder={`${KIND_LABELS[m.kind]} note`}
                    aria-label={`Note for mark ${i + 1}`}
                  />
                </li>
              ))}
            </ol>
          ) : (
            <p className="an-hint">No marks yet.</p>
          )}
          {error && (
            <p className="an-error" role="alert">
              {error}
            </p>
          )}
          <div className="an-actions">
            <button type="button" className="an-btn an-primary" onClick={() => void save()} disabled={busy || !loaded || (!marks.length && !summary.trim())}>
              {busy ? 'Saving...' : 'Save annotated copy'}
            </button>
            <button type="button" className="an-btn" onClick={requestClose} disabled={busy}>
              Cancel
            </button>
          </div>
        </aside>
      </div>
    </dialog>
  );
}
