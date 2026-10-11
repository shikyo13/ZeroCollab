import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import { isRendering, motionMedia, motionPath, previewTime } from '../lib/motionStudio';
import type { MotionCatalogue, MotionExports, MotionJob } from '../lib/motionStudio';
import './motion.css';

const modeName = (reduced: boolean) => reduced ? 'Reduced motion' : 'Full motion';
const phaseName = (job: MotionJob) => ({ starting: 'Preparing video…', rendering: 'Rendering video', encoding: 'Finishing video…', done: 'Video ready', failed: 'Export failed', cancelled: 'Export cancelled' }[job.phase] || 'Export status unknown');

export default function MotionPage() {
  const [project, setProject] = useState(() => window.activeProjectId || '');
  const [catalogue, setCatalogue] = useState<MotionCatalogue | null>(null);
  const [selected, setSelected] = useState('');
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [exports, setExports] = useState<MotionExports>({ current: null, exports: [] });
  const [html, setHtml] = useState('');
  const [error, setError] = useState('');
  const [previewError, setPreviewError] = useState('');
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [mediaBusy, setMediaBusy] = useState('');
  const [video, setVideo] = useState<{ url: string; job: MotionJob } | null>(null);
  const [reload, setReload] = useState(0);
  const [preview, setPreview] = useState({ time: 0, playing: false });
  const iframe = useRef<HTMLIFrameElement>(null);
  const time = useRef(0);
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const blobUrls = useRef(new Set<string>());
  const scene = catalogue?.scenes.find(s => s.ref === selected);
  const capability = catalogue?.export;
  const current = exports.current;
  const busy = isRendering(current);

  useRoomEvent<{ project_id?: string }>('settings', event => {
    const next = event.data?.project_id;
    if (typeof next === 'string' && next !== project) {
      epoch.current++; time.current = 0; setHtml(''); setVideo(null); setActing(false); setMediaBusy(''); setProject(next);
      setCatalogue(null); setSelected(''); setExports({ current: null, exports: [] });
    }
  });
  useEffect(() => () => { epoch.current++; }, []);
  useEffect(() => {
    const id = epoch.current;
    let cancelled = false;
    setLoading(true); setError(''); setCatalogue(null); setExports({ current: null, exports: [] });
    if (!project) { setLoading(false); return; }
    Promise.all([api<MotionCatalogue>(motionPath('scenes', project)), api<MotionExports>(motionPath('exports', project))])
      .then(([list, jobs]) => {
        if (cancelled || id !== epoch.current) return;
        if (list.project !== project) throw new Error('Project changed; reload the Motion page');
        setCatalogue(list); setExports(jobs);
        setSelected(old => list.scenes.some(s => s.ref === old) ? old : list.scenes[0]?.ref || '');
      }).catch(e => { if (!cancelled && id === epoch.current) setError(errorMessage(e)); })
      .finally(() => { if (!cancelled && id === epoch.current) setLoading(false); });
    return () => { cancelled = true; };
  }, [project, reload]);

  useEffect(() => {
    if (!busy || !project) return;
    const id = epoch.current;
    let cancelled = false, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const jobs = await api<MotionExports>(motionPath('exports', project));
        if (cancelled || id !== epoch.current) return;
        setExports(jobs);
        if (isRendering(jobs.current)) timer = setTimeout(poll, 1000);
      } catch (e) { if (!cancelled && id === epoch.current) setError(errorMessage(e)); }
    };
    timer = setTimeout(poll, 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project, busy, current?.id]);

  useEffect(() => {
    setHtml(''); setPreviewError(''); setPreview({ time: time.current, playing: false });
    if (!scene || !project) return;
    const controller = new AbortController();
    const url = motionPath('view', project) + '&scene=' + encodeURIComponent(scene.ref)
      + (reduced ? '&reduced=1' : '') + '&t=' + Math.round(time.current);
    motionMedia(url, controller.signal).then(r => r.text()).then(text => {
      if (!controller.signal.aborted) setHtml(text);
    }).catch(e => { if (!controller.signal.aborted) setPreviewError(errorMessage(e)); });
    return () => controller.abort();
  }, [project, scene?.ref, reduced, reload]);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (!html || !iframe.current || event.source !== iframe.current.contentWindow || !scene) return;
      const value = previewTime(event.data, scene.duration_ms);
      if (!value) return;
      time.current = value.time; setPreview(value); // Display only; never an API call or action.
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [html, scene]);

  useEffect(() => {
    const urls = blobUrls.current;
    return () => { urls.forEach(URL.revokeObjectURL); urls.clear(); };
  }, [project]);

  const act = async (cancel = false) => {
    if (inFlight.current || !project || (!cancel && (!scene || busy || !capability?.available))) return;
    inFlight.current = true; setActing(true); setError('');
    const id = epoch.current;
    try {
      const job = await api<MotionJob>(cancel ? '/api/runtime/motion/exports/' + encodeURIComponent(current!.id) + '/cancel' : '/api/runtime/motion/exports',
        cancel ? { project } : { project, scene: scene!.ref, reduced });
      if (id === epoch.current) setExports(old => ({ ...old, current: job }));
    } catch (e) { if (id === epoch.current) setError(errorMessage(e)); }
    finally { inFlight.current = false; if (id === epoch.current) setActing(false); }
  };

  const openVideo = async (job: MotionJob, download: boolean) => {
    if (!job.video_url || mediaBusy) return;
    const id = epoch.current;
    setMediaBusy(job.id); setError('');
    try {
      const response = await motionMedia(job.video_url);
      const blob = await response.blob();
      if (id !== epoch.current) return;
      if (!response.headers.get('Content-Type')?.startsWith('video/')) throw new Error('The export did not return a video');
      const url = URL.createObjectURL(blob); blobUrls.current.add(url);
      if (download) {
        const link = document.createElement('a'); link.href = url; link.download = job.id + '.webm'; link.click();
        setTimeout(() => { URL.revokeObjectURL(url); blobUrls.current.delete(url); }, 1000);
      } else {
        if (video) { URL.revokeObjectURL(video.url); blobUrls.current.delete(video.url); }
        setVideo({ url, job }); // Native controls, no autoplay.
      }
    } catch (e) { if (id === epoch.current) setError(errorMessage(e)); }
    finally { if (id === epoch.current) setMediaBusy(''); }
  };

  return <div className="motion-page">
    <header><h1 id="motion-title">Motion</h1><p>Preview synthetic scenes and export videos. This does not start agents or replay a session.</p></header>
    <div className="motion-toolbar"><label>Scene motion <select value={reduced ? 'reduced' : 'full'} onChange={e => setReduced(e.target.value === 'reduced')}><option value="full">Full motion</option><option value="reduced">Reduced motion</option></select></label>
      <button onClick={() => setReload(n => n + 1)} disabled={loading}>Refresh scenes and exports</button></div>
    {error && <p role="alert" className="motion-error">{error}</p>}
    {!project && <p>Open a project to preview scenes and export videos.</p>}
    {loading && <p role="status">Loading motion scenes…</p>}
    <div className="motion-grid">
      <section className="motion-panel motion-scenes" aria-labelledby="motion-scenes"><h2 id="motion-scenes">Scenes</h2>
        {!loading && catalogue && !catalogue.scenes.length && <p>No motion scenes yet.</p>}
        {catalogue?.scenes.map(s => <button key={s.ref} aria-pressed={selected === s.ref} onClick={() => { time.current = 0; setSelected(s.ref); }}><strong>{s.name}</strong><small>{s.source === 'bundled' ? 'Included scene' : 'Project scene'} · {(s.duration_ms / 1000).toFixed(1)} s · {s.beats} beats</small></button>)}
        {!!catalogue?.invalid.length && <details><summary>Unavailable scene files ({catalogue.invalid.length})</summary>{catalogue.invalid.map(item => <p key={item.file}>{item.file}: {item.error}</p>)}</details>}
      </section>
      <section className="motion-panel motion-preview" aria-labelledby="motion-preview"><h2 id="motion-preview">Preview</h2>
        {scene && <p>{scene.name} · {modeName(reduced)}</p>}
        {previewError && <p className="motion-error" role="alert">{previewError}</p>}
        {scene && !html && !previewError && <p role="status">Preparing preview…</p>}
        {html && <div className="motion-frame"><iframe key={`${project}:${selected}:${reduced}`} ref={iframe} title={`Scene preview: ${scene?.name}`} sandbox="allow-scripts" srcDoc={html} /></div>}
        {html && <p className="motion-muted">{preview.playing ? 'Playing preview' : 'Preview paused'} · {(preview.time / 1000).toFixed(1)} / {((scene?.duration_ms || 0) / 1000).toFixed(1)} s</p>}
        {!scene && !loading && <p>Choose a scene to preview.</p>}
      </section>
      <section className="motion-panel motion-export" aria-labelledby="motion-export"><h2 id="motion-export">Export</h2>
        <p>Motion: {modeName(reduced)}</p>{capability && <p className="motion-muted">WebM · VP9 · {capability.width} × {capability.height} · {capability.fps} fps</p>}
        {capability && !capability.available && <p className="motion-error">{capability.reason || 'Video export is unavailable.'}</p>}
        <button className="motion-primary" disabled={!scene || busy || acting || loading || !capability?.available} onClick={() => void act()}>Export video</button>
        {current && <div className="motion-job"><p role="status" aria-live="polite">{phaseName(current)}</p><p>{current.name} · {modeName(current.reduced)}</p>
          {current.phase === 'rendering' && current.frames > 0 && <><progress aria-label="Video frames rendered" value={current.frame} max={current.frames} /><p>Frame {current.frame} of {current.frames}</p></>}
          {current.error && <p className="motion-error">{current.error}</p>}
          {busy && <button disabled={acting} onClick={() => void act(true)}>Cancel export</button>}
        </div>}
      </section>
    </div>
    <section className="motion-panel motion-outputs" aria-labelledby="motion-outputs"><h2 id="motion-outputs">Completed videos</h2>
      {!exports.exports.length && <p>No completed videos yet.</p>}
      {exports.exports.filter(job => job.phase === 'done' && job.video_url).map(job => <div className="motion-output" key={job.id}><div><strong>{job.id}.webm</strong><p>{job.name} · {modeName(job.reduced)}{job.seconds !== undefined ? ` · ${job.seconds.toFixed(1)} s` : ''}</p></div><div className="motion-actions"><button disabled={!!mediaBusy} onClick={() => void openVideo(job, false)}>Play video</button><button disabled={!!mediaBusy} onClick={() => void openVideo(job, true)}>Download</button></div></div>)}
      {mediaBusy && <p role="status">Loading video…</p>}
      {video && <div className="motion-video"><p>{video.job.name} · {modeName(video.job.reduced)}</p><video controls src={video.url} aria-label={`Exported video: ${video.job.name}`} /></div>}
    </section>
  </div>;
}
