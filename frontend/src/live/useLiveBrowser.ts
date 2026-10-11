import { useEffect, useRef, useState } from 'react';
import { errorMessage, sessionHeaders } from '../lib/api';
import type { BrowserState } from '../workspace/Browser';

type Capture = { frame: string; time: number; project: string; url: string; title: string; ownerLabel: string; generation?: number };

/** Read-only shared focus. Poll only while this view is visible and auto-following.
 * Never publish a late frame after leaving/pinning/hiding the page. */
export function useLiveBrowser(enabled: boolean) {
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
  useEffect(() => {
    const visibility = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', visibility);
    return () => document.removeEventListener('visibilitychange', visibility);
  }, []);
  useEffect(() => {
    if (!live) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    const current = () => !stopped && active.current && !document.hidden;
    async function poll() {
      try {
        const headers = sessionHeaders();
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
          const nextUrl = URL.createObjectURL(blob), previous = objectUrl.current;
          fresh.current = true;
          objectUrl.current = nextUrl;
          const captured = { frame: nextUrl, time: Date.now() / 1000, project: next.project, url: next.url,
            title: next.title, ownerLabel: next.owner_label, generation: next.generation };
          captureRef.current = captured; setCapture(captured);
          if (previous) URL.revokeObjectURL(previous);
        } else fresh.current = false;
        setError('');
      } catch (e) { if (current()) { fresh.current = false; setError(errorMessage(e)); } }
      finally { if (current()) timer = setTimeout(poll, 1000); }
    }
    void poll();
    return () => { stopped = true; abort.abort(); clearTimeout(timer); };
  }, [live, retry]);
  useEffect(() => () => { if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);
  return { state, capture, frame: capture?.frame || '', captured: capture?.time || 0, error,
    live: live && fresh.current && !!state?.running && !!capture && !error, retry: () => setRetry(n => n + 1) };
}
