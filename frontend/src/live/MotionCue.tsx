import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

/** Keep keyed evidence recognizable as new events reorder the three cards.
 * Layout coordinates ignore animation transforms. Pausing snapshots the latest
 * layout, so resuming never plays the intervening movement as a backlog. */
export function useLiveCardMotion(root: RefObject<HTMLDivElement | null>, animate: boolean) {
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const playing = useRef(new Map<string, Animation>());
  useLayoutEffect(() => {
    const next = new Map<string, { x: number; y: number }>();
    const cards = root.current?.querySelectorAll<HTMLElement>('[data-live-source]') ?? [];
    for (const card of cards) {
      const id = card.dataset.liveSource!;
      const target = { x: card.offsetLeft, y: card.offsetTop };
      const previous = positions.current.get(id);
      next.set(id, target);
      if (!animate) { playing.current.get(id)?.cancel(); playing.current.delete(id); continue; }
      if (!previous || (previous.x === target.x && previous.y === target.y)) continue;
      // Preserve the visible position when a newer event interrupts a move.
      const transform = getComputedStyle(card).transform;
      const current = transform === 'none' ? null : new DOMMatrixReadOnly(transform);
      const x = previous.x - target.x + (current?.m41 ?? 0);
      const y = previous.y - target.y + (current?.m42 ?? 0);
      playing.current.get(id)?.cancel();
      const movement = card.animate(
        [{ transform: `translate(${x}px, ${y}px)` }, { transform: 'translate(0, 0)' }],
        { duration: 320, easing: 'cubic-bezier(.22, 1, .36, 1)' },
      );
      playing.current.set(id, movement);
      movement.onfinish = () => { if (playing.current.get(id) === movement) playing.current.delete(id); };
    }
    for (const [id, movement] of playing.current) {
      if (!next.has(id)) { movement.cancel(); playing.current.delete(id); }
    }
    positions.current = next;
  });
  useEffect(() => () => { for (const movement of playing.current.values()) movement.cancel(); playing.current.clear(); }, []);
}

/** Version-one scene meaning shared with the fixed motion comparison.
 * Timing belongs to each adapter: live cues have received wall time, exports seek
 * explicit scene time. A cue never issues room actions or changes selection. */
export interface MotionCueData {
  version: 1;
  event: 'wake' | 'tool_start' | 'tool_end' | 'task_status' | 'review_request' | 'browser_emphasis' | 'caption';
  state: string;
  from?: string;
  to?: string;
}

/** One settled state with optional, interruptible emphasis. Repeated text deltas
 * retain the same cue key, so a busy stream doesn't restart an animation. */
export function MotionCue({ cue, cueKey, animate }: { cue: MotionCueData; cueKey: string; animate: boolean }) {
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!animate || !root.current) return;
    const motion = root.current.animate(
      [{ opacity: .5, transform: 'translateY(4px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: cue.event === 'review_request' ? 450 : 220, easing: 'ease-out' },
    );
    return () => motion.cancel();
  }, [cueKey, animate, cue.event]);
  return <span ref={root} className={`lt-cue lt-cue-${cue.event}`} data-motion-event={cue.event}>
    {cue.from && <><span>{cue.from}</span><span aria-hidden="true"> → </span></>}
    <strong>{cue.state}</strong>
    {cue.event === 'review_request' && <small>Request only · no verdict yet</small>}
  </span>;
}
