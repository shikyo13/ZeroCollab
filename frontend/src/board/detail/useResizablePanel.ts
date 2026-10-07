/* Width of the task detail panel, resizable by pointer drag or arrow keys. The width
 * is published as --detail-width on the enclosing .bd-tasks grid. */
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';

const DEFAULT_WIDTH = 460;
const MIN_WIDTH = 320;
const MAX_WIDTH = 900;
const VIEWPORT_MARGIN = 32; // keep a sliver of the board visible beside the panel
const KEY_STEP = 40;

export function useResizablePanel(panelRef: RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const resize = (next: number) => setWidth(Math.max(MIN_WIDTH, Math.min(next, MAX_WIDTH, window.innerWidth - VIEWPORT_MARGIN)));

  useEffect(() => {
    const parent = panelRef.current?.closest<HTMLElement>('.bd-tasks');
    parent?.style.setProperty('--detail-width', `${width}px`);
    return () => { parent?.style.removeProperty('--detail-width'); };
  }, [width, panelRef]);

  // The panel sits on the right, so dragging left (smaller clientX) widens it.
  const handleProps = {
    'aria-valuenow': width,
    onPointerDown: (e: PointerEvent<HTMLElement>) => { drag.current = { x: e.clientX, width }; e.currentTarget.setPointerCapture(e.pointerId); },
    onPointerMove: (e: PointerEvent<HTMLElement>) => { if (drag.current) resize(drag.current.width + drag.current.x - e.clientX); },
    onPointerUp: (e: PointerEvent<HTMLElement>) => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); },
    onPointerCancel: () => { drag.current = null; },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      resize(width + (e.key === 'ArrowLeft' ? KEY_STEP : -KEY_STEP));
    },
  };
  return handleProps;
}
