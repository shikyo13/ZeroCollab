/* Task images: thumbnails on cards, a gallery in task detail with upload by
 * picker, paste or drop, and annotation into a new derived image. The task's
 * list is replaced as a whole through POST /api/runtime/tasks. */
import { useRef, useState, type ClipboardEvent, type DragEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { annotateImage } from '../annotate';
import { imageNotes, ImageViewer, uploadSource } from '../annotate/ImageViewer';
import { RASTER_IMAGE_TYPES, runtime, uploadImage } from '../lib/api';
import type { EarlierAttachment, Task, TaskAttachment } from '../lib/types';
import { ErrorText, useAction } from './shared';

/** The card shows this many newest images; adding more moves older ones to earlier images (project_room.CARD_IMAGES). */
export const CARD_IMAGES = 8;
/** Images one save may send (project_room.ATTACHMENT_INPUT): the card plus earlier images. */
export const ATTACHMENT_INPUT = 48;

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

const MOVED_REASON: Record<NonNullable<EarlierAttachment['reason']>, string> = {
  superseded: 'Replaced by a newer annotation of the same image',
  overflow: 'Moved off the card when newer images were added',
};

export function TaskAttachments({ task, channel }: { task: Task; channel: string }) {
  const [expanded, setExpanded] = useState<{ list: TaskAttachment[]; url: string } | null>(null);
  const { run, busy, error } = useAction();
  const attachments = task.attachments ?? [];
  const earlier = task.earlier_attachments ?? [];
  // Uploads and the annotation dialog are async; save against the latest list, not the one from when they began.
  const latest = useRef(attachments);
  latest.current = attachments;

  // The server never refuses for count: past CARD_IMAGES it moves older images to earlier images.
  const save = (next: TaskAttachment[]) => runtime('/tasks', { action: 'update', task_id: task.id, channel, attachments: next });

  const add = (files: File[]) => void run(async () => {
    const uploaded = await uploadAll(files);
    await save([...latest.current, ...uploaded]);
  });

  const annotate = async (attachment: TaskAttachment) => {
    const annotated = await annotateImage(uploadSource(attachment.source) || attachment.url, attachment.name);
    if (annotated) await run(() => save([...latest.current, annotated]));
  };

  const remove = (url: string) => void run(() => save(latest.current.filter(a => a.url !== url)));
  const bringBack = (item: EarlierAttachment) =>
    void run(() => save([...latest.current, { url: item.url, name: item.name, note: item.note, source: item.source }]));
  const full = attachments.length >= CARD_IMAGES;

  return (
    <ImageDropZone disabled={busy} onFiles={add}>
      {attachments.length > 0 ? (
        <ul className="bd-gallery">
          {attachments.map(a => (
            <li key={a.url} className="bd-gallery-item">
              <button type="button" className="bd-image-open" onClick={() => setExpanded({ list: attachments, url: a.url })} title="Expand image and annotation notes">
                <img src={a.url} alt={a.name} loading="lazy" />
              </button>
              <div className="bd-gallery-meta">
                <span className="bd-gallery-name" title={a.name}>
                  {a.source ? 'Annotated: ' : ''}
                  {a.name}
                </span>
                {a.note && <details className="bd-image-notes"><summary>Annotation notes</summary><p className="bd-gallery-note">{a.note}</p></details>}
                <div className="bd-actions">
                  <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => {
                    if (!uploadSource(a.source) && imageNotes(a.note).marks.length && !window.confirm('No original is recorded. New marks will be drawn over this image. Continue?')) return;
                    void annotate(a);
                  }}>
                    {uploadSource(a.source) ? 'Annotate original' : 'Annotate'}
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
      {full && <p className="bd-field-hint">The card shows the {CARD_IMAGES} newest images. Adding another moves an older one to Earlier images; nothing is deleted.</p>}
      {earlier.length > 0 && (
        <details className="bd-earlier">
          <summary>Earlier images ({earlier.length})</summary>
          <ul className="bd-gallery">
            {earlier.map(a => (
              <li key={a.url} className="bd-gallery-item bd-gallery-earlier">
                <button type="button" className="bd-image-open" onClick={() => setExpanded({ list: earlier, url: a.url })} title="Expand image and annotation notes">
                  <img src={a.url} alt={a.name} loading="lazy" />
                </button>
                <div className="bd-gallery-meta">
                  <span className="bd-gallery-name" title={a.name}>{a.source ? 'Annotated: ' : ''}{a.name}</span>
                  {a.reason && <span className="bd-field-hint">{MOVED_REASON[a.reason]}{a.moved ? ` · ${new Date(a.moved * 1000).toLocaleString()}` : ''}</span>}
                  <div className="bd-actions">
                    <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => bringBack(a)}
                      title={full ? 'Shows it on the card again; the oldest card image moves here' : 'Shows it on the card again'}>
                      Bring back
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
      {busy && <p className="bd-muted" role="status">Saving images...</p>}
      <ErrorText message={error} />
      {expanded && expanded.list.length > 0 && createPortal(<ImageViewer images={expanded.list} initial={Math.max(0, expanded.list.findIndex(a => a.url === expanded.url))}
        onClose={() => setExpanded(null)} onAnnotate={attachment => void annotate(attachment)} />, document.body)}
    </ImageDropZone>
  );
}
