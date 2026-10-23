import { useEffect, useRef, useState } from 'react';
import { isVideoAttachment, uploadMedia } from '../lib/mediaAttachment';
import './media-attachment.css';

export function MediaAttachment({ url, name = 'Rendered video', className, paused = false,
  onPlayingChange, onLoaded }: { url: string; name?: string; className?: string; paused?: boolean;
    onPlayingChange?: (playing: boolean) => void; onLoaded?: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  const source = uploadMedia(url);
  useEffect(() => { if (paused) video.current?.pause(); }, [paused]);
  useEffect(() => {
    const node = video.current;
    if (!node) return;
    const hidden = () => { if (document.hidden) node.pause(); };
    const observer = new IntersectionObserver(entries => { if (!entries[0].isIntersecting) node.pause(); });
    observer.observe(node); document.addEventListener('visibilitychange', hidden);
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', hidden); node.pause(); };
  }, [source]);
  useEffect(() => { setFailed(false); }, [source]);
  if (!source) return <p role="status">This media attachment is unavailable.</p>;
  return <div className="media-attachment" onClick={event => event.stopPropagation()}>
    {isVideoAttachment(source)
      ? <video ref={video} className={className} src={source} controls playsInline preload="metadata" aria-label={name}
          onKeyDown={event => event.stopPropagation()}
          onLoadedData={onLoaded} onError={() => setFailed(true)}
          onPlay={() => onPlayingChange?.(true)} onPause={() => onPlayingChange?.(false)} onEnded={() => onPlayingChange?.(false)} />
      : <img className={className} src={source} alt={name} loading="lazy" onLoad={onLoaded} onError={() => setFailed(true)} />}
    {failed && <p role="status">This attachment could not be played or loaded. Open the file below.</p>}
    <a href={source} target="_blank" rel="noopener noreferrer">Open full size ↗</a>
  </div>;
}
