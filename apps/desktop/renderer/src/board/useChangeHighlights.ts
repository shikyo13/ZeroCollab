/* Ids of tasks that changed while the board is open, each kept for a short highlight. */
import { useEffect, useRef, useState } from 'react';
import type { Task } from '../lib/types';

const CHANGE_HIGHLIGHT_MS = 2500;

export function useChangeHighlights(tasks: Task[] | undefined): ReadonlySet<string> {
  const [changed, setChanged] = useState<ReadonlySet<string>>(new Set());
  const seenRef = useRef<Map<string, number> | null>(null);
  const mountedRef = useRef(true);

  // The first load only records a baseline.
  useEffect(() => {
    if (!tasks) return;
    const seen = seenRef.current;
    seenRef.current = new Map(tasks.map(t => [t.id, t.updated]));
    if (!seen) return;
    const fresh = tasks.filter(t => (seen.get(t.id) ?? 0) < t.updated).map(t => t.id);
    if (!fresh.length) return;
    setChanged(cur => new Set([...cur, ...fresh]));
    // Not cleared on the next update: back-to-back changes must each fade on their own schedule.
    window.setTimeout(() => {
      if (mountedRef.current) setChanged(cur => new Set([...cur].filter(id => !fresh.includes(id))));
    }, CHANGE_HIGHLIGHT_MS);
  }, [tasks]);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  return changed;
}
