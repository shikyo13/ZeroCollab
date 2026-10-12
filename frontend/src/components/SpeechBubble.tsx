/* One speech bubble for Live and Board cards (task bbd70aad, Zero #14495: bubbles were cut off).
 * - Never cuts mid-sentence: it grows with its text up to `maxLines`, then keeps the newest lines in view
 *   and fades the top edge.
 * - Hover, keyboard focus or a click opens the whole message (scrolling inside if very long); leaving
 *   closes it, a click pins it open, Escape closes it and keeps focus on the bubble.
 * - Every size change (streaming text, a new message, open, close) eases over about 250 ms, the feel of
 *   a SwiftUI easeInOut(0.25) layout change. Reduced motion makes it instant.
 * - It reports its real box so a layout (Live) can keep everything else clear of it. While open it
 *   tells the caller, which holds the bubble's position so growing never pulls it away from the pointer. */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import './speech-bubble.css';

export const BUBBLE_LINE_PX = 17;
const PAD_Y = 12, OPEN_MAX_PX = 320, LEAVE_DELAY_MS = 180;

export interface SpeechBubbleProps {
  name: string;
  /** Owner colour (CSS colour) for the edge and the name. */
  color: string;
  text: string;
  /** Box width in px; the height follows the text. */
  width: number;
  maxLines?: number;
  reduced?: boolean;
  className?: string;
  style?: CSSProperties;
  /** The footprint the bubble occupies: while it grows, the size it grows to; while it shrinks, the larger
   * size until the shrink has finished, so a layout never hands out space the box still covers. */
  onSize?: (size: { w: number; h: number; open: boolean }) => void;
  /** Most height (px, whole box) the open bubble may take; it scrolls inside beyond that. */
  openMaxHeight?: number;
}

export function SpeechBubble({ name, color, text, width, maxLines = 5, reduced = false, className, style, onSize, openMaxHeight }: SpeechBubbleProps) {
  const inner = useRef<HTMLDivElement>(null), clip = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState(BUBBLE_LINE_PX);
  const [hover, setHover] = useState(false), [focused, setFocused] = useState(false), [pinned, setPinned] = useState(false);
  const leave = useRef<number | undefined>(undefined);
  const open = hover || focused || pinned;

  // The text's own height at this width, re-measured whenever it wraps differently.
  useLayoutEffect(() => {
    const el = inner.current; if (!el) return;
    const measure = () => setNatural(Math.max(BUBBLE_LINE_PX, Math.ceil(el.scrollHeight)));
    measure();
    const ro = new ResizeObserver(measure); ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => () => window.clearTimeout(leave.current), []);

  const cap = maxLines * BUBBLE_LINE_PX;
  const openCap = Math.max(cap, Math.min(OPEN_MAX_PX, (openMaxHeight ?? Infinity) - PAD_Y));
  const body = open ? Math.min(natural, openCap) : Math.min(natural, cap);
  const h = body + PAD_Y;
  const faded = !open && natural > cap + 1;
  // Report the footprint: grow at once (reserve the larger box), shrink only once the transition has ended.
  const box = useRef<HTMLDivElement>(null);
  const reported = useRef({ w: 0, h: 0, open: false });
  useEffect(() => {
    const report = (next: { w: number; h: number; open: boolean }) => { reported.current = next; onSize?.(next); };
    const target = { w: width, h, open };
    const prev = reported.current;
    if (reduced || matchMedia('(prefers-reduced-motion: reduce)').matches || (target.w >= prev.w && target.h >= prev.h)) { report(target); return; }
    // Shrinking: keep the union reserved, then hand back the space when the box has actually finished shrinking.
    report({ w: Math.max(prev.w, target.w), h: Math.max(prev.h, target.h), open });
    const el = box.current;
    const done = () => report(target);
    const end = (e: TransitionEvent) => { if (e.target === el && (e.propertyName === 'height' || e.propertyName === 'width')) done(); };
    el?.addEventListener('transitionend', end);
    const fallback = window.setTimeout(done, 320);   // in case no transition runs (same size, hidden tab)
    return () => { el?.removeEventListener('transitionend', end); window.clearTimeout(fallback); };
  }, [width, h, open, reduced]); // eslint-disable-line react-hooks/exhaustive-deps

  // Open and reading: follow a streaming reply only while the reader is at the bottom.
  const atBottom = useRef(true);
  useEffect(() => {
    const el = clip.current; if (!el || !open) return;
    if (atBottom.current) el.scrollTop = el.scrollHeight;
  }, [text, open, natural]);

  return <div ref={box} className={`speech-bubble${open ? ' sb-open' : ''}${reduced ? ' sb-reduced' : ''}${className ? ' ' + className : ''}`}
    style={{ ...style, width, height: h, '--c': color } as CSSProperties}
    role="note" tabIndex={0} aria-expanded={open} aria-label={`${name} says: ${text}`}
    onPointerEnter={() => { window.clearTimeout(leave.current); setHover(true); }}
    onPointerLeave={() => { window.clearTimeout(leave.current); leave.current = window.setTimeout(() => setHover(false), LEAVE_DELAY_MS); }}
    onFocus={() => setFocused(true)}
    onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}
    onClick={() => setPinned(p => !p)}
    onKeyDown={e => {
      if (e.key === 'Escape') { e.stopPropagation(); setPinned(false); setHover(false); setFocused(false); }
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); setPinned(p => !p); }
    }}>
    <div ref={clip} className={`sb-clip${faded ? ' sb-faded' : ''}`}
      onScroll={e => { const el = e.currentTarget; atBottom.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 4; }}>
      <div ref={inner} className="sb-text"><b>{name}</b> {text}</div>
    </div>
  </div>;
}
