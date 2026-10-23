import { finite, integer } from './math';

export interface Point { x: number; y: number }
export interface SeatsOptions { origin: Point; columns?: number; spacing?: Point; centered?: boolean }

/** Fixed rows; callers keep the roster index as the seat identity rather than packing on every beat. */
export function seats(count: number, options: SeatsOptions): Point[] {
  integer(count, 'seat count', 0);
  const columns = integer(options.columns ?? 3, 'columns', 1), spacing = options.spacing ?? { x: 114, y: 138 };
  [options.origin.x, options.origin.y, spacing.x, spacing.y].forEach((v, i) => finite(v, `seat input ${i}`));
  if (spacing.x < 0 || spacing.y < 0) throw new RangeError('Seat spacing must be nonnegative');
  return Array.from({ length: count }, (_, i) => {
    const row = Math.floor(i / columns), inRow = Math.min(columns, count - row * columns);
    return { x: options.origin.x + (i % columns - (options.centered === false ? 0 : (inRow - 1) / 2)) * spacing.x, y: options.origin.y + row * spacing.y };
  });
}

export interface FormationMember { id: string; order: number; group?: string | null; quiet?: boolean }
export function slotTargets(members: readonly FormationMember[], centers: Readonly<Record<string, Point>>, options: { home: SeatsOptions; group?: Omit<SeatsOptions, 'origin'> }): Record<string, Point> {
  const groups = new Map<string, FormationMember[]>(), result: Record<string, Point> = Object.create(null);
  const ordered = [...members].sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const ids = new Set<string>();
  ordered.forEach(member => {
    integer(member.order, 'roster order', 0);
    if (!member.id || ids.has(member.id)) throw new RangeError('Formation ids must be unique');
    ids.add(member.id);
    if (member.quiet || !member.group) {
      result[member.id] = seats(member.order + 1, { ...options.home, centered: false })[member.order];
    } else {
      if (!Object.hasOwn(centers, member.group)) throw new RangeError(`Missing formation center: ${member.group}`);
      const group = groups.get(member.group) ?? []; group.push(member); groups.set(member.group, group);
    }
  });
  for (const [key, group] of groups) {
    const points = seats(group.length, { ...options.group, origin: centers[key] });
    group.forEach((member, i) => { result[member.id] = points[i]; });
  }
  return result;
}
