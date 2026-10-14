import type { ProjectState } from './types';

const collections = ['tasks', 'reviews', 'followups', 'notes'] as const;
const reserved = new Set([...collections, 'project_id', 'stream_id', 'rev', 'base', '__proto__', 'constructor', 'prototype', 'last_human_message']);
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const revision = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
export function hasRevision(v: unknown): v is ProjectState & { project_id: string; stream_id: string; rev: number } {
  return object(v) && typeof v.project_id === 'string' && typeof v.stream_id === 'string' && !!v.stream_id && revision(v.rev);
}
export function isProjectState(v: unknown): v is ProjectState {
  // Older rooms may have id-less notes, which require whole-board resets.
  return object(v) && Array.isArray(v.tasks) && Array.isArray(v.notes)
    && (v.reviews === undefined || Array.isArray(v.reviews))
    && (v.followups === undefined || Array.isArray(v.followups))
    && (!('rev' in v || 'stream_id' in v) || hasRevision(v));
}
function records(v: unknown): v is (Record<string, unknown> & { id: string })[] {
  if (!Array.isArray(v)) return false;
  const ids = new Set<string>();
  return v.every(r => object(r) && typeof r.id === 'string' && !!r.id && !ids.has(r.id) && !!ids.add(r.id));
}
export type DeltaResult = { kind: 'applied'; data: ProjectState } | { kind: 'ignored' } | { kind: 'resync' };

/** Validate the entire message before replacing anything. Unchanged records retain identity. */
export function applyProjectDelta(current: ProjectState | null, delta: unknown): DeltaResult {
  if (!hasRevision(current) || !hasRevision(delta)
    || delta.project_id !== current.project_id || delta.stream_id !== current.stream_id) return { kind: 'resync' };
  if (delta.rev <= current.rev) return { kind: 'ignored' };
  const d = delta as unknown as Record<string, unknown>;
  if (!revision(d.base) || d.base !== current.rev || delta.rev <= d.base) return { kind: 'resync' };
  const upsert = d.upsert === undefined ? {} : d.upsert;
  const remove = d.remove === undefined ? {} : d.remove;
  const values = d.values === undefined ? {} : d.values;
  if (!object(upsert) || !object(remove) || !object(values)
    || Object.keys(upsert).some(k => !collections.includes(k as typeof collections[number]))
    || Object.keys(remove).some(k => !collections.includes(k as typeof collections[number]))
    || Object.keys(values).some(k => reserved.has(k))) return { kind: 'resync' };
  const next: Record<string, unknown> = { ...current, ...values, rev: delta.rev };
  for (const key of collections) {
    if (!(key in upsert) && !(key in remove)) continue;
    const before = current[key] ?? [], added = key in upsert ? upsert[key] : [], removed = key in remove ? remove[key] : [];
    if (!records(before) || !records(added) || !Array.isArray(removed)
      || removed.some(id => typeof id !== 'string' || !id) || new Set(removed).size !== removed.length) return { kind: 'resync' };
    const deleted = new Set(removed), changes = new Map(added.map(r => [r.id, r]));
    if (added.some(r => deleted.has(r.id))) return { kind: 'resync' };
    const items = before.filter(r => !deleted.has(r.id)).map(r => {
      const replacement = changes.get(r.id);
      changes.delete(r.id);
      return replacement ?? r;
    });
    next[key] = [...items, ...changes.values()];
  }
  return { kind: 'applied', data: next as unknown as ProjectState };
}
