/* Composition camera (28cb5faf). Pure geometry: a view is the world point at the frame's centre, a zoom and
 * a rotation. No DOM; scene.ts samples keyframes and render2 applies the result as one transform. */

export interface CameraView { x: number; y: number; zoom: number; rotate: number }
export type Affine = readonly [number, number, number, number, number, number];   // a b c d e f
export const IDENTITY: Affine = [1, 0, 0, 1, 0, 0];

/** Framing that fits a region [x, y, w, h] inside a W x H frame with `padding` (fraction of the region) around it. */
export function regionView(region: readonly number[], width: number, height: number, padding = 0.08): CameraView {
  const [x, y, w, h] = region;
  if (!(w > 0 && h > 0)) throw new RangeError('A camera region needs a positive width and height');
  const zoom = Math.min(width / (w * (1 + 2 * padding)), height / (h * (1 + 2 * padding)));
  return { x: x + w / 2, y: y + h / 2, zoom, rotate: 0 };
}

/** The view part-way from a to b at eased progress e. Zoom moves in log space (each frame magnifies by the same
 * ratio), and the centre follows the fixed point both framings share, so a push-in glides straight onto its
 * target instead of swinging past it. Equal zooms reduce to a straight pan. */
export function blendViews(a: CameraView, b: CameraView, e: number): CameraView {
  const zoom = Math.exp(Math.log(a.zoom) + (Math.log(b.zoom) - Math.log(a.zoom)) * e);
  const rotate = a.rotate + (b.rotate - a.rotate) * e;
  if (Math.abs(a.zoom - b.zoom) <= 1e-3 * Math.max(a.zoom, b.zoom)) {
    return { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e, zoom, rotate };
  }
  // Fixed point P: (P - a.c) * a.zoom = (P - b.c) * b.zoom.
  const px = (a.zoom * a.x - b.zoom * b.x) / (a.zoom - b.zoom), py = (a.zoom * a.y - b.zoom * b.y) / (a.zoom - b.zoom);
  const k = a.zoom / zoom;
  return { x: px + (a.x - px) * k, y: py + (a.y - py) * k, zoom, rotate };
}

/** World point -> screen point for a view on a W x H frame. */
export function toScreen(view: CameraView, width: number, height: number, p: readonly [number, number]): [number, number] {
  const r = view.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  const dx = (p[0] - view.x) * view.zoom, dy = (p[1] - view.y) * view.zoom;
  return [width / 2 + c * dx - s * dy, height / 2 + s * dx + c * dy];
}

/** Screen point -> world point (the inverse of toScreen). */
export function toWorld(view: CameraView, width: number, height: number, p: readonly [number, number]): [number, number] {
  const r = -view.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  const dx = p[0] - width / 2, dy = p[1] - height / 2;
  return [view.x + (c * dx - s * dy) / view.zoom, view.y + (s * dx + c * dy) / view.zoom];
}

/** How far screen content moves between two views, per axis: the largest displacement over the centre and four
 * inset corners. Motion blur uses it, so blur follows the camera and is zero on holds. */
export function screenMotion(prev: CameraView, now: CameraView, width: number, height: number): [number, number] {
  let mx = 0, my = 0;
  for (const [fx, fy] of [[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]]) {
    const s: [number, number] = [fx * width, fy * height];
    const before = toScreen(prev, width, height, toWorld(now, width, height, s));
    mx = Math.max(mx, Math.abs(s[0] - before[0])); my = Math.max(my, Math.abs(s[1] - before[1]));
  }
  return [mx, my];
}

/** Affine of one layer box: box pixel (u, v) -> parent pixel, matching render2's CSS
 * (left x, top y, transform-origin at the anchor, translate(-anchor) rotate scale). */
export function layerAffine(v: { x: number; y: number; w: number; h: number; ax: number; ay: number; rotate: number; sx: number; sy: number }): Affine {
  const r = v.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  const a = c * v.sx, b = s * v.sx, cc = -s * v.sy, d = c * v.sy;
  const ox = v.ax * v.w, oy = v.ay * v.h;
  return [a, b, cc, d, v.x - (a * ox + cc * oy), v.y - (b * ox + d * oy)];
}

export function compose(parent: Affine, child: Affine): Affine {
  const [a, b, c, d, e, f] = parent, [a2, b2, c2, d2, e2, f2] = child;
  return [a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f];
}

/** The axis-aligned bounds of a box region after an affine (rotated layers give their enclosing box). */
export function mapRegion(m: Affine, region: readonly number[]): [number, number, number, number] {
  const [x, y, w, h] = region, [a, b, c, d, e, f] = m;
  const pts = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]].map(([u, v]) => [a * u + c * v + e, b * u + d * v + f]);
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys);
  return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0];
}
