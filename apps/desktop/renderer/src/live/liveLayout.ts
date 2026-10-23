/* Live stage layout (task b42437b9): pure functions, no DOM. Everyone has a stable home seat by roster
 * order; people working on the same task gather into one formation with fixed seats in arrival
 * order; formations keep a gap from each other; speech takes a free spot or becomes a small cue.
 * Positions only change when a relationship changes, so a busy room does not reshuffle. */

export interface Rect { x: number; y: number; w: number; h: number }
export interface Point { x: number; y: number }

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
  /** Stopped (Zero #20226): seated on the shelf along the bottom, never in the field or a group. */
  stopped?: boolean;
}

export interface Formation { group: string; members: string[]; box: Rect }
/** thumbs: false when home rows are too tight for pictures under people; the stage then leaves them out (they
 * stay in Latest and the watch session) rather than letting a picture cover the row below. */
export interface StageLayout { seats: Map<string, Point>; formations: Formation[]; reserved: Rect[]; thumbs: boolean; shelf: Rect | null }

// w is the widest line a person draws (name or activity, both clipped to it in live.css), so nothing placed
// beside a person can cover their words (4cbce498: bubbles covered activity lines 36 px past the old 96 px box).
// h: badge 44 + name 16 + activity 14 + gaps. A quiet person's badge is 32 px; their words keep full size (quietH).
export const PERSON = { w: 116, h: 78, quietH: 66, thumbH: 46 };
const SEAT_W = 128, SEAT_H = 106, GAP = 26;
/** Top band of a formation that holds its task chip, so the chip is part of the reserved box. */
export const LABEL_H = 34;

export const overlap = (a: Rect, b: Rect) =>
  Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

/** How far a person's drawing reaches above the badge: the activity ring (6 px) plus the idle bob (3 px),
 * rounded up. Speech keeps clear of it, so a bubble never touches a working ring (installed 0.12.43 capture). */
export const BADGE_CLEAR = 10;
/** The bob's reach below and to each side (live.css lv-bob: 3 px down, 1.5 px sideways), rounded up. */
const BOB_DOWN = 3, BOB_SIDE = 2;

/** A person's footprint for speech placement: personRect raised to include the ring and bob, and the bob below and beside. */
export function speechBlocker(p: Point, quiet: boolean, hasThumb: boolean): Rect {
  const r = personRect(p, quiet, hasThumb);
  return { x: r.x - BOB_SIDE, y: r.y - BADGE_CLEAR, w: r.w + 2 * BOB_SIDE, h: r.h + BADGE_CLEAR + BOB_DOWN };
}

/** The footprint a person reserves: badge, name and their latest picture. The person's box starts 26 px above
 * their point (live.css margin-top) whatever their size; a quiet person is shorter, never narrower. */
export function personRect(p: Point, quiet: boolean, hasThumb: boolean): Rect {
  const w = PERSON.w, h = (quiet ? PERSON.quietH : PERSON.h) + (hasThumb && !quiet ? PERSON.thumbH : 0);
  return { x: p.x - w / 2, y: p.y - 26, w, h };
}

/** Home seats: a calm honeycomb grid filling the stage, row-major by roster order. Cells under a reserved area
 * (the You area, the Latest rail) are skipped and the grid grows until everyone has a free cell, so a seat is
 * never pushed out from under a panel onto a neighbour (4cbce498). */
function homeSeats(people: SeatInput[], area: Rect, reserved: Rect[]): { seats: Map<string, Point>; rowH: number } {
  const n = Math.max(1, people.length);
  const aspect = area.w / Math.max(1, area.h);
  const sorted = [...people].sort((a, b) => a.order - b.order);
  const free = (p: Point) => reserved.every(r => overlap(personRect(p, false, false), { x: r.x - 10, y: r.y - 10, w: r.w + 20, h: r.h + 20 }) === 0);
  let cells: Point[] = [], rowH = area.h;
  for (let extra = 0; extra <= n; extra++) {
    const cols = Math.max(1, Math.min(n + extra, Math.round(Math.sqrt((n + extra) * aspect))));
    const rows = Math.ceil((n + extra) / cols);
    const cw = area.w / cols, ch = area.h / rows;
    rowH = ch;
    const all: Point[] = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const shift = cols > 1 ? (r % 2 ? cw / 4 : -cw / 4) : 0;
      all.push({ x: area.x + cw * (c + 0.5) + shift, y: area.y + ch * (r + 0.5) });
    }
    cells = all.filter(free);
    if (cells.length >= n) break;
    if (extra === n) cells = all;   // a stage too small to clear: fill every cell; the overlap check reports it
  }
  const seats = new Map<string, Point>();
  sorted.forEach((p, i) => seats.set(p.id, cells[i] ?? cells[cells.length - 1]));
  return { seats, rowH };
}

/** The stopped shelf (Zero #20226): one row of small seats along the bottom, right of the Latest rail, wrapping to a
 * second row when the room has many stopped people. Its height for `count` people across `width` px. */
export const SHELF = { slot: 112, row: 70, head: 18, pad: 10 };
export function shelfRect(count: number, stage: Rect, rail: Rect | null): Rect | null {
  if (!count) return null;
  const x = rail ? rail.x + rail.w + 16 : stage.x + 12, w = stage.x + stage.w - 12 - x;
  if (w < SHELF.slot) return null;
  const rows = Math.ceil(count / Math.max(1, Math.floor(w / SHELF.slot))), h = SHELF.head + rows * SHELF.row;
  return { x, y: stage.y + stage.h - SHELF.pad - h, w, h };
}

/** Place everyone. `reserved` areas (the You area, a lifted picture) are never used for seats. `fixed` reserved areas
 * (the Latest rail) shape the home grid itself; the others can change size (the You area grows while you answer),
 * so a home seat under one steps aside instead, and the rest of the room keeps its seats. Stopped people sit on
 * the `shelf` (shelfRect), in roster order. The field above it keeps a home for everyone, stopped or not, so stopping
 * or resuming someone moves only them (their home waits empty), never the rest of the room. */
export function layoutStage(input: SeatInput[], stage: Rect, reserved: Rect[] = [], previous?: Map<string, Rect>, fixed: Rect[] = [], shelf: Rect | null = null): StageLayout {
  const area = { x: stage.x + 60, y: stage.y + 70, w: Math.max(120, stage.w - 120), h: Math.max(120, stage.h - 130) };
  if (shelf) area.h = Math.max(120, Math.min(area.h, shelf.y - 8 - area.y));
  const shelved = shelf ? input.filter(p => p.stopped).sort((a, b) => a.order - b.order) : [];
  const { seats: home, rowH: homeRowH } = homeSeats(input, area, shelf ? [...fixed, shelf] : fixed);
  if (shelf) {
    const perRow = Math.max(1, Math.floor(shelf.w / SHELF.slot));
    shelved.forEach((p, i) => home.set(p.id, { x: shelf.x + SHELF.slot * (i % perRow + 0.5), y: shelf.y + SHELF.head + 26 + SHELF.row * Math.floor(i / perRow) }));
  }
  // A cramped stage (a narrow window, the watch session open) keeps every person clear by leaving pictures out.
  const roomy = homeRowH >= PERSON.h + PERSON.thumbH + 16;
  const people = (roomy ? input : input.map(p => ({ ...p, hasThumb: false }))).map(p => shelf && p.stopped ? { ...p, group: undefined, quiet: true, hasThumb: false } : p);
  // A home seat under a resizable reserved area steps below it, else beside it, else to the nearest spot that covers
  // nobody else's seat; nobody sits under something you are using.
  const blocked = [...fixed, ...(shelf ? [shelf] : [])];
  // Someone stepping aside keeps this much air from everyone else (a touching name and badge read as one person).
  const STEP_GAP = 14;
  for (const p of people) {
    if (shelf && p.stopped) continue;
    const seat = home.get(p.id)!;
    for (const r of reserved) {
      if (overlap(personRect(seat, p.quiet, p.hasThumb), r) === 0) continue;
      const others = people.filter(q => q.id !== p.id).map(q => personRect(home.get(q.id)!, q.quiet, q.hasThumb));
      const options = [{ x: seat.x, y: r.y + r.h + 36 }, { x: r.x + r.w + PERSON.w / 2 + 10, y: seat.y }, { x: r.x - PERSON.w / 2 - 10, y: seat.y }];
      const fits = (q: Point) => { const rect = personRect(q, p.quiet, p.hasThumb);
        return rect.x >= stage.x && rect.y >= stage.y && rect.x + rect.w <= stage.x + stage.w && rect.y + rect.h <= stage.y + stage.h - 10
          && [...reserved, ...blocked].every(x => overlap(rect, x) === 0) && others.every(o => overlap({ x: o.x - STEP_GAP, y: o.y - STEP_GAP, w: o.w + 2 * STEP_GAP, h: o.h + 2 * STEP_GAP }, rect) === 0); };
      home.set(p.id, options.find(fits) ?? nearestFit(seat, fits) ?? options[0]);
    }
  }
  const byGroup = new Map<string, SeatInput[]>();
  for (const p of people) if (p.group && !p.quiet) (byGroup.get(p.group) ?? byGroup.set(p.group, []).get(p.group)!).push(p);

  // A formation exists only when two or more people share the work; a lone worker stays home.
  // Formations are placed one at a time in a stable order (earliest joined first), so an existing
  // group keeps its spot when a new one forms.
  const groups = [...byGroup].filter(([, m]) => m.length > 1).map(([group, members]) => {
    members.sort((a, b) => (a.joinedAt ?? 0) - (b.joinedAt ?? 0) || a.order - b.order);
    return { group, members, since: members[0].joinedAt ?? 0, label: members.find(m => m.label)?.label };
  }).sort((a, b) => a.since - b.since || (a.group < b.group ? -1 : 1));
  // A group with no room keeps its members at home, which makes their homes obstacles for every other group,
  // so placement runs again without it until nothing new fails (at most once per group).
  const failed = new Set<string>();
  for (;;) {
    const placed = placeGroups(groups.filter(g => !failed.has(g.group)));
    if (placed.failed.every(g => failed.has(g))) return { ...placed, reserved, thumbs: roomy, shelf };
    placed.failed.forEach(g => failed.add(g));
  }

  function placeGroups(active: typeof groups): { seats: Map<string, Point>; formations: Formation[]; failed: string[] } {
    const seats = new Map(home);
    const members = new Set(active.flatMap(g => g.members.map(m => m.id)));
    const staying = people.filter(p => !members.has(p.id));
    // Obstacles: busy people at home and anyone showing a picture are hard. Quiet people are soft: a full room has
    // no gap between home rows wide enough for a group (4cbce498: a pair needs ~190 px, rows sit ~186 px apart),
    // so a group may take quiet people's spots, and they move into the homes its members just left.
    // The shelf is hard too: a group never forms over it, and nobody on it is moved into the field.
    const hard: Rect[] = [...reserved, ...(shelf ? [shelf] : []), ...staying.filter(p => !p.quiet || p.hasThumb).map(p => personRect(home.get(p.id)!, p.quiet, p.hasThumb))];
    const soft = staying.filter(p => p.quiet && !p.hasThumb && !(shelf && p.stopped));
    const quietRect = (q: Point) => personRect(q, true, false);
    const boxes: Rect[] = [], vacated: Point[] = [];
    const inArea = (r: Rect) => r.x >= area.x - 40 && r.y >= area.y - 50 && r.x + r.w <= area.x + area.w + 40 && r.y + r.h <= area.y + area.h + 40;
    const near = (r: Rect, t: Rect, gap: number) => overlap({ x: r.x - gap, y: r.y - gap, w: r.w + 2 * gap, h: r.h + 2 * gap }, t) > 0;
    const formations: Formation[] = [], fails: string[] = [];
    for (const g of active) {
      const homes = g.members.map(m => home.get(m.id)!);
      const cx = homes.reduce((s, h) => s + h.x, 0) / homes.length, cy = homes.reduce((s, h) => s + h.y, 0) / homes.length;
      const cols = g.members.length <= 3 ? g.members.length : Math.ceil(g.members.length / 2);
      const rows = Math.ceil(g.members.length / cols);
      // The chip clips its title, so a long task name never widens the group past its seats by much.
      const chipW = Math.min(260, 7 * (g.label?.length ?? 24) + 64);
      const rowH = SEAT_H + (g.members.some(m => m.hasThumb) ? PERSON.thumbH : 0);
      const w = Math.max(cols * SEAT_W, chipW), h = rows * rowH + LABEL_H;
      const free = [...vacated, ...homes];
      // A spot works if nothing hard is near it and every quiet person it covers has a free home to move to.
      const tryAt = (r: Rect, displace: boolean): Map<string, Point> | null => {
        if (!inArea(r) || [...hard, ...boxes].some(t => near(r, t, GAP))) return null;
        const covered = soft.filter(p => near(r, quietRect(seats.get(p.id)!), GAP));
        if (!covered.length) return new Map();
        if (!displace) return null;
        const blocked = [...hard, ...boxes, r];
        const settled = soft.filter(p => !covered.includes(p)).map(p => quietRect(seats.get(p.id)!));
        const open = free.filter(c => !blocked.some(t => near(quietRect(c), t, 8)) && !settled.some(t => near(quietRect(c), t, 4)));
        if (open.length < covered.length) return null;
        const moves = new Map<string, Point>();
        for (const p of covered) {
          const at = seats.get(p.id)!;
          const best = open.sort((a, b) => Math.hypot(a.x - at.x, a.y - at.y) - Math.hypot(b.x - at.x, b.y - at.y)).shift()!;
          moves.set(p.id, best);
        }
        return moves;
      };
      let spot: { r: Rect; moves: Map<string, Point> } | null = null;
      // A group that already has a spot keeps it while it still works (nothing reshuffles needlessly).
      const before = previous?.get(g.group);
      if (before) { const r = { x: before.x + before.w / 2 - w / 2, y: before.y, w, h }, moves = tryAt(r, true); if (moves) spot = { r, moves }; }
      // Nearest spot to the members' homes, spiralling out: one that displaces nobody, else one
      // whose quiet people can move into the members' empty homes.
      for (const displace of spot ? [] : [false, true]) {
        for (let radius = 0; radius <= 620 && !spot; radius += 20) {
          const steps = radius === 0 ? 1 : Math.ceil((2 * Math.PI * radius) / 20);
          for (let k = 0; k < steps; k++) {
            const ang = (k / steps) * 2 * Math.PI;
            const r = { x: cx + radius * Math.cos(ang) - w / 2, y: cy + radius * Math.sin(ang) - h / 2, w, h };
            const moves = tryAt(r, displace);
            if (moves) { spot = { r, moves }; break; }
          }
        }
        if (spot) break;
      }
      if (!spot) { fails.push(g.group); continue; }
      const { r, moves } = spot, mid = r.x + r.w / 2;
      g.members.forEach((m, i) => {
        const row = Math.floor(i / cols), inRow = Math.min(cols, g.members.length - row * cols), c = i % cols;
        seats.set(m.id, { x: mid - inRow * SEAT_W / 2 + SEAT_W * (c + 0.5), y: r.y + LABEL_H + rowH * row + 34 });
      });
      for (const [id, q] of moves) seats.set(id, q);
      vacated.splice(0, vacated.length, ...free.filter(c => ![...moves.values()].includes(c)));
      boxes.push(r);
      formations.push({ group: g.group, members: g.members.map(m => m.id), box: r });
    }
    return { seats, formations, failed: fails };
  }
}

/** The nearest point to `from` that `fits`, spiralling out in 20 px rings, or null within 600 px. */
function nearestFit(from: Point, fits: (q: Point) => boolean): Point | null {
  for (let radius = 20; radius <= 600; radius += 20) {
    const steps = Math.ceil((2 * Math.PI * radius) / 24);
    for (let k = 0; k < steps; k++) {
      const ang = (k / steps) * 2 * Math.PI, q = { x: from.x + radius * Math.cos(ang), y: from.y + radius * Math.sin(ang) };
      if (fits(q)) return q;
    }
  }
  return null;
}

/** Where a picture lifted out of its owner's thumb goes (Zero #20226): beside the owner, clear of `hard` rects (the
 * You area, the rail, the shelf, group floors with their gap) and of the owner, displacing as few of `movable`
 * (people at home, who then step aside) as it can, at most `MAX_DISPLACED`. Tries `sizes` largest first; null when
 * none fits, and the picture stays in its thumb. */
const MAX_DISPLACED = 3;
export function placeLift(owner: Rect, sizes: { w: number; h: number }[], hard: Rect[], movable: Rect[], stage: Rect): Rect | null {
  const o = { x: owner.x + owner.w / 2, y: owner.y + owner.h / 2 };
  for (const size of sizes) {
    let best: { r: Rect; cost: number } | null = null;
    for (let y = stage.y + 12; y + size.h <= stage.y + stage.h - 12; y += 12) {
      for (let x = stage.x + 12; x + size.w <= stage.x + stage.w - 12; x += 12) {
        const r = { x, y, ...size }, pad = { x: x - 8, y: y - 8, w: size.w + 16, h: size.h + 16 };
        if (overlap(pad, owner) || hard.some(h => overlap(pad, h))) continue;
        const displaced = movable.filter(m => overlap(pad, m) > 0).length;
        if (displaced > MAX_DISPLACED) continue;
        // Nearest edge to the owner, so it reads as theirs; each displaced person costs as much as 400 px of distance.
        const dx = Math.max(r.x - o.x, 0, o.x - (r.x + r.w)), dy = Math.max(r.y - o.y, 0, o.y - (r.y + r.h));
        const cost = displaced * 400 + Math.hypot(dx, dy);
        if (!best || cost < best.cost) best = { r, cost };
      }
    }
    if (best) return best.r;
  }
  return null;
}

export type BubbleSide ='right' | 'left' | 'above' | 'below' | 'upRight' | 'upLeft' | 'downRight' | 'downLeft';

/** Whether a bubble fits here: inside the stage, with a few pixels of air on every side, so it never sits flush
 * against a chip, a panel or a name. */
export function bubbleClear(rect: Rect, blockers: Rect[], bounds: Rect) {
  return rect.x >= bounds.x && rect.y >= bounds.y && rect.x + rect.w <= bounds.x + bounds.w && rect.y + rect.h <= bounds.y + bounds.h
    && blockers.every(b => overlap({ x: rect.x - 4, y: rect.y - 4, w: rect.w + 8, h: rect.h + 8 }, b) === 0);
}

/** A clear spot for one speech bubble next to its person, else (reach > 0) the nearest clear spot within reach
 * px (marked `ring`), or null when every spot covers something: the caller then shows a small cue by the person instead (text
 * is never dropped, only moved). */
export function placeBubble(p: Point, size: { w: number; h: number }, blockers: Rect[], bounds: Rect, prefer?: BubbleSide, reach = 0): { side: BubbleSide; rect: Rect; ring?: true } | null {
  const o = PERSON.w / 2 + 8;
  const c: Record<BubbleSide, Point> = {
    right: { x: p.x + o, y: p.y - size.h / 2 }, left: { x: p.x - o - size.w, y: p.y - size.h / 2 },
    above: { x: p.x - size.w / 2, y: p.y - 34 - size.h }, below: { x: p.x - size.w / 2, y: p.y + 56 },
    upRight: { x: p.x + o, y: p.y - 30 - size.h }, upLeft: { x: p.x - o - size.w, y: p.y - 30 - size.h },
    downRight: { x: p.x + o, y: p.y + 40 }, downLeft: { x: p.x - o - size.w, y: p.y + 40 },
  };
  const order: BubbleSide[] = ['right', 'left', 'upRight', 'upLeft', 'above', 'below', 'downRight', 'downLeft'];
  if (prefer) order.unshift(prefer);
  const clear = (rect: Rect) => bubbleClear(rect, blockers, bounds);
  for (const side of order) {
    const rect = { ...c[side], ...size };
    if (clear(rect)) return { side, rect };
  }
  // Farther out (4cbce498: in a full room the eight spots touching a person are usually taken, and most speakers
  // fell back to a cue): the nearest clear spot within reach, nearest first. The bubble names its speaker, so it
  // still reads as theirs. One above the person grows upward, away from them; one below grows down.
  for (let radius = 40; radius <= reach; radius += 20) {
    const steps = Math.ceil((2 * Math.PI * radius) / 24);
    for (let k = 0; k < steps; k++) {
      const ang = (k / steps) * 2 * Math.PI;
      const rect = { x: p.x + radius * Math.cos(ang) - size.w / 2, y: p.y + radius * Math.sin(ang) - size.h / 2, ...size };
      if (clear(rect)) return { side: rect.y + rect.h / 2 < p.y ? 'above' : 'below', rect, ring: true };
    }
  }
  return null;
}

/** Critically damped spring toward a target: arrives without overshoot or wobble.
 * Exact for any dt (analytic), so a paused or throttled tab never explodes. */
export function springStep(x: number, v: number, target: number, dt: number, omega = 9): [number, number] {
  const d = x - target, e = Math.exp(-omega * dt);
  const a = (v + omega * d) * dt;
  return [target + (d + a) * e, (v - omega * a) * e];
}

/* ---------- Who is working with whom (task 4cbce498, Zero #18047) ----------
 * One shared task is rare (each task has one owner), so the stage also gathers people joined by a real, current
 * relation: a pending review (reviewer with the owner), an open follow-up, or an exchange in the last two
 * minutes ('recent': an @mention, a verdict, a follow-up answer or a handoff between them). A pair gathers
 * only while at least one of them is active, and an idle person joins someone busy
 * only when the relation is about that person's current work (or names no task), so a stale request never
 * pulls anyone away from what they are actually doing. Pure and deterministic for a given input. */
export interface CollabPerson { id: string; active: boolean; task_id?: string; task_title?: string }
export interface CollabLink { kind: 'review' | 'followup' | 'recent'; from: string; to: string; task_id?: string; task_title?: string; at: number }
export interface CollabGroup { key: string; why: 'task' | CollabLink['kind']; title?: string; anchor?: string }

/** How long each relation keeps a pair together after it last changed (s). An active person's own task keeps it longer. */
export const LINK_HOLD_S: Record<CollabLink['kind'], number> = { review: 45 * 60, followup: 45 * 60, recent: 120 };
const PRIORITY: Record<CollabLink['kind'], number> = { review: 0, followup: 1, recent: 2 };
export const MAX_GROUP = 6;

export function collabGroups(people: CollabPerson[], links: CollabLink[], now: number): Map<string, CollabGroup> {
  const byId = new Map(people.map(p => [p.id, p]));
  const out = new Map<string, CollabGroup>();
  const size = new Map<string, number>();
  const join = (id: string, g: CollabGroup) => { out.set(id, g); size.set(g.key, (size.get(g.key) ?? 0) + 1); };
  for (const p of people) if (p.active && p.task_id) join(p.id, { key: p.task_id, why: 'task', title: p.task_title });
  const live = links.filter(l => l.from !== l.to && byId.has(l.from) && byId.has(l.to))
    .filter(l => now - l.at <= LINK_HOLD_S[l.kind] || [l.from, l.to].some(id => byId.get(id)!.active && l.task_id && byId.get(id)!.task_id === l.task_id))
    .sort((a, b) => PRIORITY[a.kind] - PRIORITY[b.kind] || b.at - a.at);
  for (const l of live) {
    const a = byId.get(l.from)!, b = byId.get(l.to)!;
    if (!a.active && !b.active) continue;
    const ga = out.get(a.id), gb = out.get(b.id);
    if (ga && gb) continue;                                   // both already placed: no merging
    if (ga || gb) {
      // One is placed: the other joins that group if the relation is about that work or names no task.
      const [g, other] = ga ? [ga, b] : [gb!, a];
      if (l.task_id && g.why === 'task' && g.key !== l.task_id) continue;
      if ((size.get(g.key) ?? 0) >= MAX_GROUP) continue;
      join(other.id, g);
      continue;
    }
    // Neither placed: the pair forms its group around the relation's task, else around the active one.
    const anchor = a.active ? a : b;
    const g: CollabGroup = l.task_id ? { key: l.task_id, why: l.kind, title: l.task_title } : { key: `with:${anchor.id}`, why: l.kind, anchor: anchor.id };
    if ((size.get(g.key) ?? 0) + 2 > MAX_GROUP) continue;
    join(a.id, g); join(b.id, g);
  }
  return out;
}

/** When the next relation in `links` stops holding a pair together (s since epoch), or Infinity. */
export function nextLinkExpiry(links: CollabLink[], now: number): number {
  return links.reduce((m, l) => { const end = l.at + LINK_HOLD_S[l.kind]; return end > now ? Math.min(m, end) : m; }, Infinity);
}
