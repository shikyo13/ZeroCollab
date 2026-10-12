import { useEffect, useRef, useState } from 'react';
import { errorMessage, sessionHeaders } from '../lib/api';
import type { BrowserState } from '../workspace/Browser';

type Capture = { frame: string; time: number; project: string; url: string; title: string; ownerLabel: string; generation?: number; source: string };

/** Read-only shared focus. Poll only while this view is visible and enabled.
 * With `frameUrl` (a Live artifact's own /api/runtime/browser/frame?project=&page= address), frames are
 * bound to that artifact's project and page: a different active page never shows under its label, and a
 * change of artifact clears the previous capture before any new pixels appear (design 3e3c7401 F2).
 * Disabling (pause, pin) freezes the last capture rather than clearing it.
 * Never publish a late frame after leaving/pinning/hiding the page. */
export function useLiveBrowser(enabled: boolean, frameUrl?: string) {
  const [state, setState] = useState<BrowserState>();
  const [capture, setCapture] = useState<Capture>();
  const captureRef = useRef<Capture | undefined>(undefined);
  const [error, setError] = useState('');
  const [visible, setVisible] = useState(() => !document.hidden);
  const [retry, setRetry] = useState(0);
  const objectUrl = useRef('');
  const live = enabled && visible;
  const fresh = useRef(false);
  if (!live) fresh.current = false;
  const active = useRef(live); active.current = live;
  const bound = useRef(frameUrl); bound.current = frameUrl;
  useEffect(() => {
    const visibility = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', visibility);
    return () => document.removeEventListener('visibilitychange', visibility);
  }, []);
  // A different artifact: drop the old pixels at once, so they are never shown under the new label.
  useEffect(() => {
    if (captureRef.current && captureRef.current.source !== (frameUrl ?? '')) {
      captureRef.current = undefined; setCapture(undefined); fresh.current = false; setError('');
      if (objectUrl.current) { URL.revokeObjectURL(objectUrl.current); objectUrl.current = ''; }
    }
  }, [frameUrl]);
  useEffect(() => {
    if (!live) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    const source = frameUrl ?? '';
    const current = () => !stopped && active.current && !document.hidden && (bound.current ?? '') === source;
    const publish = (blob: Blob, meta: Omit<Capture, 'frame' | 'time' | 'source'>) => {
      const nextUrl = URL.createObjectURL(blob), previous = objectUrl.current;
      fresh.current = true;
      objectUrl.current = nextUrl;
      const captured = { ...meta, frame: nextUrl, time: Date.now() / 1000, source };
      captureRef.current = captured; setCapture(captured);
      if (previous) URL.revokeObjectURL(previous);
    };
    async function poll() {
      try {
        const headers = sessionHeaders();
        if (frameUrl) {
          // Bound to the artifact's own page: no global status, no other page's pixels.
          const picture = await fetch(frameUrl, { headers, signal: abort.signal });
          if (!picture.ok) throw new Error(picture.status === 404 ? 'That browser page is closed; the last frame stays shown.' : `Browser frame unavailable (${picture.status})`);
          const blob = await picture.blob();
          if (!current()) return;
          const query = new URL(frameUrl, location.origin).searchParams;
          publish(blob, { project: query.get('project') || '', url: '', title: '', ownerLabel: '' });
          setError('');
          return;
        }
        const response = await fetch('/api/runtime/browser', { headers, signal: abort.signal });
        if (!response.ok) throw new Error(`Browser status unavailable (${response.status})`);
        const next = await response.json() as BrowserState & { generation?: number };
        if (!current()) return;
        const previousCapture = captureRef.current;
        if (!previousCapture || previousCapture.project !== next.project || previousCapture.url !== next.url
          || previousCapture.title !== next.title || previousCapture.generation !== next.generation) fresh.current = false;
        setState(next);
        if (next.running) {
          const picture = await fetch('/api/runtime/browser/frame?project=' + encodeURIComponent(next.project), { headers, signal: abort.signal });
          if (!picture.ok) throw new Error(`Browser frame unavailable (${picture.status})`);
          const blob = await picture.blob();
          if (!current()) return;
          publish(blob, { project: next.project, url: next.url, title: next.title, ownerLabel: next.owner_label, generation: next.generation });
        } else fresh.current = false;
        setError('');
      } catch (e) { if (current()) { fresh.current = false; setError(errorMessage(e)); } }
      finally { if (current()) timer = setTimeout(poll, 1000); }
    }
    void poll();
    return () => { stopped = true; abort.abort(); clearTimeout(timer); };
  }, [live, retry, frameUrl]);
  useEffect(() => () => { if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);
  return { state, capture, frame: capture?.frame || '', captured: capture?.time || 0, error,
    live: live && fresh.current && (!!frameUrl || !!state?.running) && !!capture && !error, retry: () => setRetry(n => n + 1) };
}
