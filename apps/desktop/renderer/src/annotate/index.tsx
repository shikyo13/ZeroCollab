/* Open the annotation dialog for any same-origin image and upload the result.
 * Used by Board attachments and exposed as window.ZeroAnnotate so the legacy
 * chat lightbox can offer the same tool without importing React code. */
import { createRoot } from 'react-dom/client';
import { uploadImage } from '../lib/api';
import type { TaskAttachment } from '../lib/types';
import { AnnotationEditor } from './AnnotationEditor';
import { ImageViewer } from './ImageViewer';

declare global {
  interface Window {
    ZeroAnnotate?: (src: string, onSaved: (attachment: TaskAttachment) => void, title?: string) => void;
    ZeroImageReview?: (images: TaskAttachment[], initial: number, onSaved: (attachment: TaskAttachment) => void) => void;
  }
}

function baseName(src: string): string {
  return decodeURIComponent(src.split(/[?#]/)[0].split('/').pop() || 'image').replace(/\.[a-z0-9]+$/i, '');
}

/** Resolves with the uploaded annotated copy, or null if the user cancels. */
export function annotateImage(src: string, title = baseName(src)): Promise<TaskAttachment | null> {
  return new Promise(resolve => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    let result: TaskAttachment | null = null;
    const close = () => {
      resolve(result);
      // Unmount after React finishes dispatching the dialog's close event.
      setTimeout(() => {
        root.unmount();
        host.remove();
      });
    };
    root.render(
      <AnnotationEditor
        src={src}
        title={title}
        onClose={close}
        onSave={async ({ blob, note }) => {
          const name = `${baseName(src)}-annotated.png`;
          const uploaded = await uploadImage(blob, name);
          const source = src.includes('/uploads/') ? '/uploads/' + src.split('/uploads/')[1].split(/[?#]/)[0] : undefined;
          result = { url: uploaded.url, name, note, ...(source ? { source } : {}) };
        }}
      />,
    );
  });
}

export function installAnnotateHook() {
  window.ZeroImageReview = (images, initial, onSaved) => {
    if (!images.length) return;
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const previousFocus = document.activeElement as HTMLElement | null;
    root.render(<ImageViewer images={images} initial={Math.max(0, Math.min(initial, images.length - 1))}
      onClose={() => setTimeout(() => { root.unmount(); host.remove(); previousFocus?.focus(); })}
      onAnnotate={attachment => { void annotateImage(attachment.url, attachment.name).then(saved => saved && onSaved(saved)); }} />);
  };
  window.ZeroAnnotate = (src, onSaved, title) => {
    void annotateImage(src, title).then(attachment => attachment && onSaved(attachment));
  };
}
