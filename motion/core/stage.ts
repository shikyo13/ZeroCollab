/* Live stage layout (task b42437b9): pure functions, no DOM. Everyone has a stable home seat by roster
 * order; people working on the same task gather into one formation with fixed seats in arrival
 * order; formations keep a gap from each other; speech takes a free spot or becomes a small cue.
 * Positions only change when a relationship changes, so a busy room does not reshuffle. */

export interface Rect { x: number; y: number; w: number; h: number }
import type { Point } from './layout';

export interface SeatInput {
  id: string;
  order: number;
  /** Task this person is actively on; people sharing one gather. Empty: stays home. */
  group?: string;
  /** The group's chip text, so the formation is at least as wide as its chip. */
  label?: string;
  /** When they joined the group (stable seat order inside it). */
  joinedAt?: number;
  quiet: boolean;
  hasThumb: boolean;
}

export interface Formation { group: string; members: string[]; box: Rect }
export interface StageLayout { seats: Map<string, Point>; formations: Formation[]; reserved: Rect[] }

export const PERSON = { w: 96, h: 74, thumbH: 46, quietScale: 0.72 };
const SEAT_W = 120, SEAT_H = 104, GAP = 26;
/** Top band of a formation that holds its task chip, so the chip is part of the reserved box. */
export const LABEL_H = 34;

export const overlap = (a: Rect, b: Rect) =>
  Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

/** The footprint a person reserves: badge, name and their latest picture. */
export function personRect(p: Point, quiet: boolean, hasThumb: boolean): Rect {
  const s = quiet ? PERSON.quietScale : 1;
  const w = PERSON.w * s, h = PERSON.h * s + (hasThumb && !quiet ? PERSON.thumbH : 0);
  return { x: p.x - w / 2, y: p.y - 26 * s, w, h };
}

/** Home seats: a calm honeycomb grid filling the stage, row-major by roster order. */
function homeSeats(people: SeatInput[], area: Rect): Map<string, Point> {
  const n = Math.max(1, people.length);
  const aspect = area.w / Math.max(1, area.h);
  const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt(n * aspect))));
  const rows = Math.ceil(n / cols);
  const cw = area.w / cols, ch = area.h / rows;
  const seats = new Map<string, Point>();
  [...people].sort((a, b) => a.order - b.order).forEach((p, i) => {
    const r = Math.floor(i / cols), c = i % cols;
    const shift = r % 2 ? cw / 4 : -cw / 4;
    seats.set(p.id, { x: area.x + cw * (c + 0.5) + (cols > 1 ? shift : 0), y: area.y + ch * (r + 0.5) });
  });
  return seats;
}

/** Formation placement avoids reserved areas when space permits. Callers inspect footprints for crowding. */
export function layoutStage(people: SeatInput[], stage: Rect, reserved: Rect[] = [], previous?: Map<string, Rect>): StageLayout {
  const area = { x: stage.x + 60, y: stage.y + 70, w: Math.max(120, stage.w - 120), h: Math.max(120, stage.h - 130) };
  const home = homeSeats(people, area);
  const seats = new Map(home);
  const byGroup = new Map<string, SeatInput[]>();
  for (const p of people) if (p.group && !p.quiet) (byGroup.get(p.group) ?? byGroup.set(p.group, []).get(p.group)!).push(p);

  // A formation exists only when two or more people share the work; a lone worker stays home.
  // Formations are placed one at a time in a stable order (earliest joined first), so an existing
  // group keeps its spot when a new one forms.
  const groups = [...byGroup].filter(([, m]) => m.length > 1).map(([group, members]) => {
    members.sort((a, b) => (a.joinedAt ?? 0) - (b.joinedAt ?? 0) || a.order - b.order);
    return { group, members, since: members[0].joinedAt ?? 0, label: members.find(m => m.label)?.label };
  }).sort((a, b) => a.since - b.since || (a.group < b.group ? -1 : 1));
  const members = new Set(groups.flatMap(g => g.members.map(m => m.id)));
  // People staying home are fixed obstacles; members' own homes are free, since they have left them.
  const taken: Rect[] = [...reserved, ...people.filter(p => !members.has(p.id)).map(p => personRect(home.get(p.id)!, p.quiet, p.hasThumb))];
  const clear = (r: Rect) => r.x >= area.x - 40 && r.y >= area.y - 50 && r.x + r.w <= area.x + area.w + 40 && r.y + r.h <= area.y + area.h + 40
    && taken.every(t => overlap({ x: r.x - GAP, y: r.y - GAP, w: r.w + 2 * GAP, h: r.h + 2 * GAP }, t) === 0);

  const formations: Formation[] = [];
  for (const g of groups) {
    const homes = g.members.map(m => home.get(m.id)!);
    const cx = homes.reduce((s, h) => s + h.x, 0) / homes.length, cy = homes.reduce((s, h) => s + h.y, 0) / homes.length;
    const cols = g.members.length <= 3 ? g.members.length : Math.ceil(g.members.length / 2);
    const rows = Math.ceil(g.members.length / cols);
    const chipW = Math.min(320, 7 * (g.label?.length ?? 24) + 64);
    let placed: { r: Rect; seatW: number; rowH: number } | null = null;
    // A group that already has a spot keeps it while it is still clear (nothing reshuffles needlessly).
    const before = previous?.get(g.group);
    if (before) {
      const rowH = SEAT_H + (g.members.some(m => m.hasThumb) ? PERSON.thumbH : 0);
      const w = Math.max(cols * SEAT_W, chipW), h = rows * rowH + LABEL_H;
      const r = { x: before.x + before.w / 2 - w / 2, y: before.y, w, h };
      if (clear(r)) placed = { r, seatW: SEAT_W, rowH };
    }
    // Nearest free spot to the members' homes, spiralling out; tighten the seats before giving up.
    for (const scale of placed ? [] : [1, 0.86, 0.74]) {
      const seatW = SEAT_W * scale, rowH = (SEAT_H + (g.members.some(m => m.hasThumb) ? PERSON.thumbH : 0)) * scale;
      const w = Math.max(cols * seatW, chipW), h = rows * rowH + LABEL_H;
      for (let radius = 0; radius <= 520 && !placed; radius += 20) {
        const steps = radius === 0 ? 1 : Math.ceil((2 * Math.PI * radius) / 20);
        for (let k = 0; k < steps; k++) {
          const ang = (k / steps) * 2 * Math.PI;
          const r = { x: cx + radius * Math.cos(ang) - w / 2, y: cy + radius * Math.sin(ang) - h / 2, w, h };
          if (clear(r)) { placed = { r, seatW, rowH }; break; }
        }
      }
      if (placed) break;
    }
    // No free space at all (a very small stage): sit at the centroid; the overlap check will report it.
    if (!placed) { const w = Math.max(cols * SEAT_W, chipW), rowH = SEAT_H; placed = { r: { x: cx - w / 2, y: cy - (rows * rowH + LABEL_H) / 2, w, h: rows * rowH + LABEL_H }, seatW: SEAT_W, rowH }; }
    const { r, seatW, rowH } = placed;
    const mid = r.x + r.w / 2;
    g.members.forEach((m, i) => {
      const row = Math.floor(i / cols), inRow = Math.min(cols, g.members.length - row * cols), c = i % cols;
      seats.set(m.id, { x: mid - inRow * seatW / 2 + seatW * (c + 0.5), y: r.y + LABEL_H + rowH * row + 34 });
    });
    taken.push(r);
    formations.push({ group: g.group, members: g.members.map(m => m.id), box: r });
  }
  return { seats, formations, reserved };
}

export type BubbleSide = 'right' | 'left' | 'above' | 'below' | 'upRight' | 'upLeft' | 'downRight' | 'downLeft';

/** A clear spot for one speech bubble next to its person, or null when every spot covers something:
 * the caller then shows a small cue by the person instead (text is never dropped, only moved). */
export function placeBubble(p: Point, size: { w: number; h: number }, blockers: Rect[], bounds: Rect, prefer?: BubbleSide): { side: BubbleSide; rect: Rect } | null {
  const o = PERSON.w / 2 + 8;
  const c: Record<BubbleSide, Point> = {
    right: { x: p.x + o, y: p.y - size.h / 2 }, left: { x: p.x - o - size.w, y: p.y - size.h / 2 },
    above: { x: p.x - size.w / 2, y: p.y - 34 - size.h }, below: { x: p.x - size.w / 2, y: p.y + 56 },
    upRight: { x: p.x + o, y: p.y - 30 - size.h }, upLeft: { x: p.x - o - size.w, y: p.y - 30 - size.h },
    downRight: { x: p.x + o, y: p.y + 40 }, downLeft: { x: p.x - o - size.w, y: p.y + 40 },
  };
  const order: BubbleSide[] = ['right', 'left', 'upRight', 'upLeft', 'above', 'below', 'downRight', 'downLeft'];
  if (prefer) order.unshift(prefer);
  for (const side of order) {
    const rect = { ...c[side], ...size };
    if (rect.x < bounds.x || rect.y < bounds.y || rect.x + rect.w > bounds.x + bounds.w || rect.y + rect.h > bounds.y + bounds.h) continue;
    if (blockers.every(b => overlap(rect, b) === 0)) return { side, rect };
  }
  return null;
}

