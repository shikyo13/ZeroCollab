import type { ProjectState } from './types';
import { applyProjectDelta, hasRevision, isProjectState } from './projectDelta';

export interface ProjectSnapshot { data: ProjectState | null; error: string; baseline: boolean }

/** One recovery owner for Board, Follow and Live. A fetch cannot roll back a newer push,
 * cross a project/reconnect generation, or silently swallow a gap observed during it. */
export function createProjectStateStore(load: () => Promise<unknown>) {
  let snapshot: ProjectSnapshot = { data: null, error: '', baseline: true };
  const listeners = new Set<() => void>();
  let pending: Promise<void> | null = null;
  let epoch = 0, gaps = 0, pushes = 0;
  let recovering = true;
  let project: string | undefined;
  let stream: string | undefined;
  let wanted: { stream_id: string; rev: number } | null = null;
  const retired = new Set<string>();
  const publish = (data: ProjectState | null, baseline: boolean, error = '') => {
    snapshot = { data, baseline, error };
    listeners.forEach(fn => fn());
  };
  function accept(data: ProjectState, baseline: boolean, pushed: boolean) {
    const previous = snapshot.data;
    if (project !== undefined && data.project_id !== undefined && data.project_id !== project) return;
    if (hasRevision(data)) {
      if (retired.has(data.stream_id)) return;
      if (hasRevision(previous) && previous.stream_id === data.stream_id && data.rev < previous.rev) return;
      if (stream && stream !== data.stream_id) retired.add(stream);
      if (hasRevision(previous) && previous.stream_id !== data.stream_id) retired.add(previous.stream_id);
      stream = data.stream_id;
      project = data.project_id;
    }
    const sameProject = previous?.project_id === data.project_id;
    publish(pushed ? { ...data, last_human_message: sameProject ? previous?.last_human_message ?? '' : '' } : data, baseline);
  }
  const refresh = (): Promise<void> => {
    if (pending) return pending;
    pending = (async () => {
      for (let attempt = 0; attempt < 3; attempt++) {
        const generation = epoch, missed = gaps, pushed = pushes;
        try {
          const data = await load();
          if (generation !== epoch) continue;
          if (!isProjectState(data)) throw new Error('The board response is incomplete. Refresh to try again.');
          if (project !== undefined && data.project_id !== undefined && data.project_id !== project) continue;
          const latest = snapshot.data;
          // A gap gives us a lower bound even when no applicable delta was received.
          const reaches = (v: ProjectState | null) => !wanted || (hasRevision(v)
            && v.stream_id === wanted.stream_id && v.rev >= wanted.rev);
          if (!reaches(data) && !reaches(latest)) continue;
          if (hasRevision(data) || pushed === pushes) accept(data, true, false);
          if (missed !== gaps && !hasRevision(data)) continue;
          recovering = false;
          wanted = null;
        } catch (e) {
          if (generation !== epoch) continue;
          publish(snapshot.data, snapshot.baseline, e instanceof Error ? e.message : String(e));
        }
        return;
      }
      publish(snapshot.data, snapshot.baseline, 'The board changed while reconnecting. Refresh to catch up.');
    })().finally(() => { pending = null; });
    return pending;
  };
  function recover() { recovering = true; gaps++; void refresh(); }
  return {
    read: () => snapshot,
    subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
    refresh,
    invalidate(nextProject?: string) {
      epoch++; recovering = true; wanted = null;
      if (nextProject !== undefined && nextProject !== project) {
        if (stream) retired.add(stream);
        project = nextProject; stream = undefined;
        publish(null, true);
      }
      void refresh();
    },
    full(value: unknown) {
      if (!isProjectState(value)) { recover(); return; }
      if (hasRevision(value) && retired.has(value.stream_id)) return;
      const previous = snapshot.data;
      if (project !== undefined && value.project_id !== undefined && value.project_id !== project) { recover(); return; }
      // A new stream makes any earlier in-flight GET obsolete.
      const changed = hasRevision(value) && stream !== value.stream_id;
      if (changed) epoch++;
      pushes++;
      accept(value, recovering || changed || !previous || hasRevision(value), true);
      // Full resets are baselines; old servers' subsequent full pushes remain live updates.
      if (!wanted || (hasRevision(value) && value.stream_id === wanted.stream_id && value.rev >= wanted.rev)) {
        recovering = false; wanted = null;
      }
    },
    delta(value: unknown) {
      if (hasRevision(value) && retired.has(value.stream_id)) return;
      if (hasRevision(value) && (project === undefined || project === value.project_id)) {
        if (stream && stream !== value.stream_id && wanted?.stream_id !== value.stream_id) { epoch++; recovering = true; }
        project = value.project_id;
      }
      const result = applyProjectDelta(snapshot.data, value);
      if (result.kind === 'ignored') return;
      if (result.kind === 'resync' || recovering) {
        if (hasRevision(value) && value.project_id === project) {
          wanted = { stream_id: value.stream_id, rev: Math.max(value.rev, wanted?.stream_id === value.stream_id ? wanted.rev : 0) };
        }
        recover(); return;
      }
      pushes++;
      publish(result.data, false);
    },
  };
}
