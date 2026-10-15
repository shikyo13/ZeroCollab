/* A picture lifted out of its owner's thumb (Zero #20226: "see annotations happen in real time"). A new markup,
 * screenshot or upload grows out of the thumb to a readable size in a clear spot beside its owner; a markup's marks
 * are traced on the original in the order they were drawn, numbered as room_markup numbers them, so the result
 * matches the posted image. Someone driving the shared browser lifts its live frame, and each click ripples where
 * it landed. It holds while the hold bar runs (hover, focus or Pause hold it), then shrinks back into the thumb.
 * Everything that moves is transform or opacity on its own layer, except the mark strokes (a small SVG). */
import { useState, type CSSProperties } from 'react';
import type { LiveArtifact, LiveMark } from './liveFeed';
import type { Point, Rect } from './liveLayout';
import { useLiveBrowser } from './useLiveBrowser';

export const LIFT_HEAD = 28;
/** When a mark starts tracing (s after the lift starts): after the lift has grown (live.css: .25 s wait, .55 s grow),
 * one every 0.55 s. */
export const markAt = (i: number) => 0.85 + 0.55 * i;
/** How long a lift holds: at least 6 s, and 3 s after its last mark is numbered. */
export const liftHold = (marks: number) => Math.max(6, markAt(marks) + 3);
const KIND: Record<LiveArtifact['kind'], string> = { screenshot: 'screenshot', markup: 'annotation', upload: 'picture', browser_frame: 'shared browser' };

export interface Lift {
  owner: string;
  name: string;
  color: string;
  art: LiveArtifact;
  /** The image shown: a markup's original (its marks are drawn over it), else the picture itself. */
  src: string;
  /** The image's own size, which sets the mark coordinates and the box's aspect. */
  nat: { w: number; h: number };
  rect: Rect;
  /** The owner's thumb, relative to rect: the lift grows out of it and shrinks back into it. */
  origin: Point;
  closing: boolean;
}

export function LiveLift({ lift, paused, onOpen, onDone, onGone }: { lift: Lift; paused: boolean; onOpen: () => void; onDone: () => void; onGone: () => void }) {
  const { art, rect, nat } = lift, browser = art.kind === 'browser_frame';
  const frame = useLiveBrowser(browser && !paused && !lift.closing, browser ? art.url : undefined);
  // A browser frame's real size is known once it loads; until then the box's own aspect stands in for it.
  const [frameNat, setFrameNat] = useState(nat);
  const picH = rect.h - LIFT_HEAD, marks = art.kind === 'markup' && art.source_url ? art.marks ?? [] : [];
  const what = KIND[art.kind] + (marks.length ? ` · ${marks.length} mark${marks.length > 1 ? 's' : ''}` : '');
  const src = browser ? frame.frame : lift.src;
  const style = { left: rect.x, top: rect.y, width: rect.w, height: rect.h, '--c': lift.color, '--ox': `${lift.origin.x}px`, '--oy': `${lift.origin.y}px`,
    '--hold': `${liftHold(marks.length)}s` } as CSSProperties;
  return <button type="button" className={`lv-lift${lift.closing ? ' lv-lift-out' : ''}`} style={style} onClick={onOpen}
    title={`Open ${lift.name}'s ${KIND[art.kind]} in the watch panel`} aria-label={`${lift.name} shared ${KIND[art.kind] === 'annotation' ? 'an' : 'a'} ${what}. Open it.`}
    onAnimationEnd={e => { if (e.animationName === 'lv-lift-out') onGone(); else if (e.animationName === 'lv-lift-hold' && !lift.closing) onDone(); }}>
    <span className="lv-lift-head"><i aria-hidden="true" /><b>{lift.name}</b><span>{what}</span><small aria-hidden="true">open ↗</small></span>
    <span className="lv-lift-pic" style={{ height: picH }}>
      {src ? <img src={src} alt="" draggable={false}
        onLoad={e => { const i = e.currentTarget; if (browser && i.naturalWidth && (i.naturalWidth !== frameNat.w || i.naturalHeight !== frameNat.h)) setFrameNat({ w: i.naturalWidth, h: i.naturalHeight }); }} />
        : <span className="lv-lift-wait">{frame.error || 'Waiting for the browser frame'}</span>}
      {marks.length > 0 && <Marks marks={marks} nat={nat} />}
      {browser && art.point && <Click key={`${art.at}`} at={art.point} box={{ w: rect.w, h: picH }} nat={frameNat} />}
    </span>
    {/* A new browser action restarts the hold (the key), so a lift stays up while its owner keeps driving. */}
    <span key={browser ? `${art.at}` : 'hold'} className="lv-lift-hold" aria-hidden="true" />
  </button>;
}

/** The marks as room_markup.render_markup draws them (same line width, head, badge size and placement), each traced in
 * order: the stroke draws on, an arrow's head lands, then its number pops in. */
function Marks({ marks, nat }: { marks: LiveMark[]; nat: { w: number; h: number } }) {
  const { w: W, h: H } = nat, line = Math.max(2, Math.round(Math.min(W, H) / 250)), badge = Math.max(9, Math.round(Math.min(W, H) / 45));
  // meet + centred matches the picture's object-fit: contain, so marks stay on it when a tall picture letterboxes.
  return <svg className="lv-marks" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
    {marks.map((m, i) => {
      const x1 = m.x1 * (W - 1), y1 = m.y1 * (H - 1), x2 = m.x2 * (W - 1), y2 = m.y2 * (H - 1), at = markAt(i);
      const path = m.kind === 'box' ? `M${Math.min(x1, x2)} ${Math.min(y1, y2)}H${Math.max(x1, x2)}V${Math.max(y1, y2)}H${Math.min(x1, x2)}Z`
        : m.kind === 'arrow' ? `M${x1} ${y1}L${x2} ${y2}` : '';
      const angle = Math.atan2(y2 - y1, x2 - x1), head = line * 5;
      const tip = m.kind === 'arrow' ? [[x2, y2], ...[-Math.PI / 7, Math.PI / 7].map(d => [x2 - head * Math.cos(angle + d), y2 - head * Math.sin(angle + d)])].map(p => p.join(',')).join(' ') : '';
      const [bx0, by0] = m.kind === 'box' ? [Math.min(x1, x2), Math.min(y1, y2)] : [x1, y1];
      const bx = Math.max(badge, Math.min(W - badge, bx0)), by = Math.max(badge, Math.min(H - badge, by0));
      const rgb = [1, 3, 5].map(k => parseInt(m.color.slice(k, k + 2), 16)), ink = rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722 > 140 ? '#0b0d10' : '#ffffff';
      return <g key={i} style={{ '--at': `${at}s` } as CSSProperties}>
        {path && <path className="lv-mark-line" d={path} pathLength={1} fill="none" stroke="#0b0d10" strokeWidth={line + 2} strokeLinejoin="miter" />}
        {path && <path className="lv-mark-line" d={path} pathLength={1} fill="none" stroke={m.color} strokeWidth={line} strokeLinejoin="miter" />}
        {tip && <polygon className="lv-mark-pop lv-mark-tip" points={tip} fill={m.color} />}
        <g className="lv-mark-pop lv-mark-badge">
          <circle cx={bx} cy={by} r={badge} fill={m.color} stroke="#0b0d10" strokeWidth={1} />
          <text x={bx} y={by} fill={ink} fontSize={badge * 1.1} fontWeight={700} textAnchor="middle" dominantBaseline="central">{i + 1}</text>
        </g>
      </g>;
    })}
  </svg>;
}

/** Where a click or scroll landed on the shared browser: a ripple at that point of the frame (object-fit: contain). */
function Click({ at, box, nat }: { at: { x: number; y: number }; box: { w: number; h: number }; nat: { w: number; h: number } }) {
  const s = Math.min(box.w / nat.w, box.h / nat.h), ox = (box.w - nat.w * s) / 2, oy = (box.h - nat.h * s) / 2;
  const x = ox + Math.min(1, Math.max(0, at.x)) * nat.w * s, y = oy + Math.min(1, Math.max(0, at.y)) * nat.h * s;
  return <span className="lv-click" style={{ transform: `translate(${x}px, ${y}px)` }} aria-hidden="true"><i /></span>;
}
