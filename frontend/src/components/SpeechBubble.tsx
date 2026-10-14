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
import { clamp, easing } from '../lib/motionCore';
import './speech-bubble.css';

export const BUBBLE_LINE_PX = 17;
const PAD_Y = 12, OPEN_MAX_PX = 320, LEAVE_DELAY_MS = 180, EDGE_PX = 2;
/** The size ease, shared by the CSS transition and the frame-driven render mode so both move identically. */
export const BUBBLE_SIZE_MS = 250;

/** Replies are written in markdown; a bubble reads them as plain sentences. Bold markers, code ticks,
 * link targets and heading marks go (stream-safe: an unclosed marker never shows as "**"); the words stay. */
export function plainSpeech(text: string): string {
  return text.replace(/\*\*/g, '').replace(/`+/g, '').replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, '$1').replace(/^#{1,6}\s+/gm, '');
}

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
  /** Render mode (motion ui layers, d7fe16c8): a controlled open state, overriding hover, focus and pin. */
  open?: boolean;
  /** Render mode: a controlled scroll position while open ('end' follows the newest line). */
  scrollTop?: number | 'end';
  /** Render mode: where the size is coming from and how long ago it changed. With it, no CSS transition or
   * timer runs; the box height is the 250 ms ease between the two measured sizes at ageMs, so any frame can be
   * drawn on its own and matches the live transition. */
  settle?: { fromText: string; fromOpen: boolean; ageMs: number };
}

export function SpeechBubble({ name, color, text, width, maxLines = 5, reduced = false, className, style, onSize, openMaxHeight, open: openProp, scrollTop, settle }: SpeechBubbleProps) {
  const inner = useRef<HTMLDivElement>(null), clip = useRef<HTMLDivElement>(null), from = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState(BUBBLE_LINE_PX), [fromNatural, setFromNatural] = useState(BUBBLE_LINE_PX);
  const [hover, setHover] = useState(false), [focused, setFocused] = useState(false), [pinned, setPinned] = useState(false);
  const leave = useRef<number | undefined>(undefined);
  const open = openProp ?? (hover || focused || pinned);
  const frameMode = !!settle;

  // The text's own height at this width, re-measured whenever it wraps differently.
  useLayoutEffect(() => {
    const el = inner.current; if (!el) return;
    const measure = () => setNatural(Math.max(BUBBLE_LINE_PX, Math.ceil(el.scrollHeight)));
    measure();
    const ro = new ResizeObserver(measure); ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Render mode measures synchronously on every change, so a frame never waits for a ResizeObserver.
  useLayoutEffect(() => {
    if (!frameMode) return;
    if (inner.current) setNatural(Math.max(BUBBLE_LINE_PX, Math.ceil(inner.current.scrollHeight)));
    if (from.current) setFromNatural(Math.max(BUBBLE_LINE_PX, Math.ceil(from.current.scrollHeight)));
  }, [frameMode, text, settle?.fromText, width, name]);
  useEffect(() => () => window.clearTimeout(leave.current), []);

  const cap = maxLines * BUBBLE_LINE_PX;
  const openCap = Math.max(cap, Math.min(OPEN_MAX_PX, (openMaxHeight ?? Infinity) - PAD_Y));
  const bodyFor = (isOpen: boolean, textHeight: number) => isOpen ? Math.min(textHeight, openCap) : Math.min(textHeight, cap);
  const target = bodyFor(open, natural) + PAD_Y;
  const h = settle && !reduced
    ? (bodyFor(settle.fromOpen, fromNatural) + PAD_Y) + (target - (bodyFor(settle.fromOpen, fromNatural) + PAD_Y)) * easing['ease-in-out'](clamp(settle.ageMs / BUBBLE_SIZE_MS))
    : target;
  const faded = !open && natural > cap + 1;
  // Report the footprint: grow at once (reserve the larger box), shrink only once the transition has ended.
  const box = useRef<HTMLDivElement>(null);
  const reported = useRef({ w: 0, h: 0, open: false });
  useEffect(() => {
    const report = (next: { w: number; h: number; open: boolean }) => { reported.current = next; onSize?.(next); };
    const target = { w: width, h, open };
    const prev = reported.current;
    if (frameMode || reduced || matchMedia('(prefers-reduced-motion: reduce)').matches || (target.w >= prev.w && target.h >= prev.h)) { report(target); return; }
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
  // While an open bubble scrolls, the edge with more text beyond it fades, so a cut line reads as "more".
  const [more, setMore] = useState({ above: false, below: false });
  const edges = () => {
    const el = clip.current; if (!el) return;
    atBottom.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    const next = { above: el.scrollTop > EDGE_PX, below: el.scrollTop + el.clientHeight < el.scrollHeight - EDGE_PX };
    setMore(m => m.above === next.above && m.below === next.below ? m : next);
  };
  // A layout effect, so the scroll position and its edge fades are settled before paint (render mode needs that).
  useLayoutEffect(() => {
    const el = clip.current; if (!el || !open) return;
    if (scrollTop !== undefined) el.scrollTop = scrollTop === 'end' ? el.scrollHeight : scrollTop;
    else if (atBottom.current) el.scrollTop = el.scrollHeight;
    edges();
  }, [text, open, natural, h, scrollTop]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = plainSpeech(text);

  return <div ref={box} className={`speech-bubble${open ? ' sb-open' : ''}${reduced ? ' sb-reduced' : ''}${frameMode ? ' sb-frame' : ''}${className ? ' ' + className : ''}`}
    style={{ ...style, width, height: h, '--c': color } as CSSProperties}
    role="note" tabIndex={0} aria-expanded={open} aria-label={`${name} says: ${shown}`}
    onPointerEnter={() => { window.clearTimeout(leave.current); setHover(true); }}
    onPointerLeave={() => { window.clearTimeout(leave.current); leave.current = window.setTimeout(() => setHover(false), LEAVE_DELAY_MS); }}
    onFocus={() => setFocused(true)}
    onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}
    onClick={() => setPinned(p => !p)}
    onKeyDown={e => {
      if (e.key === 'Escape') { e.stopPropagation(); setPinned(false); setHover(false); setFocused(false); }
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); setPinned(p => !p); }
    }}>
    <div ref={clip} className={`sb-clip${faded ? ' sb-faded' : ''}${open && more.above ? ' sb-more-above' : ''}${open && more.below ? ' sb-more-below' : ''}`}
      onScroll={edges}>
      <div ref={inner} className="sb-text"><b>{name}</b> {shown}</div>
    </div>
    {settle && <div ref={from} className="sb-text sb-measure" aria-hidden="true"><b>{name}</b> {plainSpeech(settle.fromText)}</div>}
  </div>;
}
