/* Task images: thumbnails on cards, a gallery in task detail with upload by
 * picker, paste or drop, and annotation into a new derived image. The task's
 * list is replaced as a whole through POST /api/runtime/tasks. */
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type ReactNode } from 'react';
import { annotateImage } from '../annotate';
import { RASTER_IMAGE_TYPES, runtime, uploadImage } from '../lib/api';
import type { Task, TaskAttachment } from '../lib/types';
import { ErrorText, useAction } from './shared';

export const MAX_ATTACHMENTS = 8;

function imageFiles(list: FileList | DataTransferItemList | null | undefined): File[] {
  if (!list) return [];
  const files: File[] = [];
  for (const item of Array.from(list as ArrayLike<File | DataTransferItem>)) {
    const file = item instanceof File ? item : item.kind === 'file' ? item.getAsFile() : null;
    if (file && RASTER_IMAGE_TYPES.includes(file.type)) files.push(file);
  }
  return files;
}

export async function uploadAll(files: File[]): Promise<TaskAttachment[]> {
  const uploaded: TaskAttachment[] = [];
  for (const file of files) {
    const result = await uploadImage(file, file.name || 'pasted-image.png');
    uploaded.push({ url: result.url, name: file.name || 'Pasted image' });
  }
  return uploaded;
}

export function CardThumbnails({ attachments }: { attachments?: TaskAttachment[] }) {
  if (!attachments?.length) return null;
  return (
    <div className="bd-thumbs" aria-label={`${attachments.length} image${attachments.length === 1 ? '' : 's'}`}>
      {attachments.slice(0, 3).map(a => (
        <img key={a.url} src={a.url} alt="" loading="lazy" />
      ))}
      {attachments.length > 3 && <span className="bd-more">+{attachments.length - 3}</span>}
    </div>
  );
}

/** Paste/drop/pick intake shared by the gallery and the new-task form. */
export function ImageDropZone({
  disabled,
  onFiles,
  children,
}: {
  disabled?: boolean;
  onFiles: (files: File[]) => void;
  children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = (files: File[]) => {
    if (files.length && !disabled) onFiles(files);
  };
  return (
    <div
      className={`bd-dropzone${over ? ' bd-dropzone-over' : ''}`}
      onPaste={(e: ClipboardEvent) => {
        const files = imageFiles(e.clipboardData?.items);
        if (files.length) {
          // The legacy chat composer listens on document; keep board images out of it.
          e.preventDefault();
          e.stopPropagation();
          take(files);
        }
      }}
      onDragEnter={(e: DragEvent) => e.stopPropagation()}
      onDragOver={(e: DragEvent) => {
        e.stopPropagation();
        if (disabled || !Array.from(e.dataTransfer.items).some(i => i.kind === 'file')) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={(e: DragEvent) => {
        e.stopPropagation();
        setOver(false);
      }}
      onDrop={(e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setOver(false);
        take(imageFiles(e.dataTransfer.files));
      }}
    >
      {children}
      <div className="bd-actions">
        <button type="button" className="bd-btn" disabled={disabled} onClick={() => input.current?.click()}>
          Add images
        </button>
        <span className="bd-field-hint">or paste / drop here. PNG, JPEG, GIF or WebP.</span>
      </div>
      <input
        ref={input}
        type="file"
        accept={RASTER_IMAGE_TYPES.join(',')}
        multiple
        hidden
        onChange={e => {
          take(imageFiles(e.target.files));
          e.target.value = '';
        }}
      />
    </div>
  );
}

export function TaskAttachments({ task, channel }: { task: Task; channel: string }) {
  const [expanded, setExpanded] = useState<TaskAttachment | null>(null);
  const viewer = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (expanded) viewer.current?.showModal(); }, [expanded]);
  const { run, busy, error, setError } = useAction();
  const attachments = task.attachments ?? [];
  const room = MAX_ATTACHMENTS - attachments.length;
  // Uploads and the annotation dialog are async; save against the latest list, not the one from when they began.
  const latest = useRef(attachments);
  latest.current = attachments;

  const save = (next: TaskAttachment[]) => runtime('/tasks', { action: 'update', task_id: task.id, channel, attachments: next });

  const add = (files: File[]) => {
    if (files.length > room) {
      setError(`A task holds up to ${MAX_ATTACHMENTS} images; remove one first.`);
      return;
    }
    void run(async () => {
      const uploaded = await uploadAll(files);
      await save([...latest.current, ...uploaded]);
    });
  };

  const annotate = async (attachment: TaskAttachment) => {
    if (room < 1) {
      setError(`A task holds up to ${MAX_ATTACHMENTS} images; remove one before saving an annotated copy.`);
      return;
    }
    const annotated = await annotateImage(attachment.url, attachment.name);
    if (annotated) await run(() => save([...latest.current, annotated]));
  };

  const remove = (url: string) => void run(() => save(latest.current.filter(a => a.url !== url)));

  return (
    <ImageDropZone disabled={busy || room < 1} onFiles={add}>
      {attachments.length > 0 ? (
        <ul className="bd-gallery">
          {attachments.map(a => (
            <li key={a.url} className="bd-gallery-item">
              <button type="button" className="bd-image-open" onClick={() => setExpanded(a)} title="Expand image and annotation notes">
                <img src={a.url} alt={a.name} loading="lazy" />
              </button>
              <div className="bd-gallery-meta">
                <span className="bd-gallery-name" title={a.name}>
                  {a.source ? 'Annotated: ' : ''}
                  {a.name}
                </span>
                {a.note && <details className="bd-image-notes"><summary>Annotation notes</summary><p className="bd-gallery-note">{a.note}</p></details>}
                <div className="bd-actions">
                  <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => void annotate(a)}>
                    Annotate
                  </button>
                  <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => remove(a.url)}>
                    Remove
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="bd-muted">No images. Screenshots and mockups added here are visible to the team; agents open them on demand.</p>
      )}
      {busy && <p className="bd-muted" role="status">Saving images...</p>}
      <ErrorText message={error} />
      {expanded && <dialog ref={viewer} className="bd-image-viewer" onKeyDown={e => e.stopPropagation()} onClose={() => setExpanded(null)}>
        <header><h2>{expanded.name}</h2><button type="button" onClick={() => viewer.current?.close()} aria-label="Close expanded image">Close ✕</button></header>
        <div className="bd-image-viewer-content"><div className="bd-image-canvas"><img src={expanded.url} alt={expanded.name} /></div><aside><h3>Annotation notes</h3><p>{expanded.note || 'No annotation notes for this image.'}</p><a href={expanded.url} target="_blank" rel="noopener">Open original size ↗</a><button type="button" onClick={() => { viewer.current?.close(); void annotate(expanded); }}>Annotate a copy</button></aside></div>
      </dialog>}
    </ImageDropZone>
  );
}
