import { cubicBezier } from '../../../motion/core/math';
import { frameToMilliseconds, type RationalFps } from '../../../motion/core/frame';
import {
  BOARD_MOTION_CURVE, BOARD_FLIGHT_MS, BOARD_REFLOW_MS, BOARD_CUE_MS,
  BOARD_REDUCED_MS, BOARD_FLIGHT_OPACITY, BOARD_DONE_MIN_SCALE, BOARD_REDUCED_OPACITY,
} from '../board/boardMotion';
import type { CardPositions } from './layout';
import type { UiData, UiLayer } from './types';

interface Move {
  frame: number;
  before: UiData;
  after: UiData;
  changed: ReadonlySet<string>;
}
const ease = cubicBezier(...BOARD_MOTION_CURVE);
const progress = (age: number, duration: number) => Math.min(1, age / duration);
type Endpoints = (event: Move) => { before: CardPositions; after: CardPositions };

/** Authored status boundaries, independent of later text/scroll snapshots. */
export function boardMotion(layer: UiLayer) {
  const events: Move[] = [];
  let before = layer.data;
  for (const state of layer.states ?? []) {
    const statuses = new Map(before.tasks.map(task => [task.id, task.status]));
    const changed = new Set(state.data.tasks.filter(task =>
      statuses.has(task.id) && statuses.get(task.id) !== task.status).map(task => task.id));
    if (changed.size) events.push({ frame: state.frame, before, after: state.data, changed });
    before = state.data;
  }
  const activeMoves = (frame: number) => {
    const latest = new Map<string, Move>();
    let reflow: Move | undefined;
    for (const event of events) {
      if (event.frame > frame) break;
      reflow = event;
      for (const id of event.changed) latest.set(id, event);
    }
    return { latest, reflow };
  };
  const prepare = (frame: number, fps: RationalFps, reduced: boolean, endpoints: Endpoints) => {
    if (reduced) return;
    const { latest, reflow } = activeMoves(frame);
    const needed = new Set<Move>();
    if (reflow && frameToMilliseconds(frame - reflow.frame, fps) < BOARD_REFLOW_MS) needed.add(reflow);
    for (const event of latest.values())
      if (frameToMilliseconds(frame - event.frame, fps) < BOARD_FLIGHT_MS) needed.add(event);
    for (const event of needed) endpoints(event);
  };
  const apply = (host: HTMLElement, frame: number, fps: RationalFps, reduced: boolean, endpoints: Endpoints) => {
    const { latest, reflow } = activeMoves(frame);
    const cards = new Map(Array.from(host.querySelectorAll<HTMLElement>('[data-task-id]'))
      .map(card => [card.dataset.taskId!, card]));
    const ageOf = (event: Move) => frameToMilliseconds(frame - event.frame, fps);
    if (!reduced && reflow && ageOf(reflow) < BOARD_REFLOW_MS) {
      const positions = endpoints(reflow);
      const remaining = 1 - ease(progress(ageOf(reflow), BOARD_REFLOW_MS));
      for (const [id, card] of cards) {
        if (reflow.changed.has(id)) continue;
        const from = positions.before.get(id), to = positions.after.get(id);
        if (from && to) card.style.transform =
          `translate(${(from.x - to.x) * remaining}px, ${(from.y - to.y) * remaining}px)`;
      }
    }
    for (const [id, event] of latest) {
      const card = cards.get(id), age = ageOf(event);
      if (!card) continue;
      if (reduced) {
        if (age < BOARD_REDUCED_MS) card.style.opacity = String(
          BOARD_REDUCED_OPACITY + (1 - BOARD_REDUCED_OPACITY) * progress(age, BOARD_REDUCED_MS));
        continue;
      }
      if (age < BOARD_FLIGHT_MS + BOARD_CUE_MS) card.classList.add('bd-motion-cue');
      if (age >= BOARD_FLIGHT_MS) continue;
      const positions = endpoints(event);
      const from = positions.before.get(id), to = positions.after.get(id);
      if (!from || !to || !from.width || !from.height) continue;
      const ghost = from.clone.cloneNode(true) as HTMLElement;
      ghost.removeAttribute('data-task-id'); ghost.removeAttribute('id');
      ghost.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
      ghost.setAttribute('aria-hidden', 'true'); ghost.inert = true;
      ghost.classList.add('bd-card-flight');
      const p = ease(progress(age, BOARD_FLIGHT_MS));
      const sx = 1 + (to.width / from.width - 1) * p;
      const sy = card.dataset.taskStatus === 'done'
        ? 1 + (Math.max(BOARD_DONE_MIN_SCALE, to.height / from.height) - 1) * p : 1;
      Object.assign(ghost.style, {
        left: `${from.x}px`, top: `${from.y}px`, width: `${from.width}px`, height: `${from.height}px`,
        transform: `translate(${(to.x - from.x) * p}px, ${(to.y - from.y) * p}px) scale(${sx}, ${sy})`,
        opacity: String(BOARD_FLIGHT_OPACITY.from + (BOARD_FLIGHT_OPACITY.to - BOARD_FLIGHT_OPACITY.from) * p),
      });
      host.append(ghost);
    }
  };
  return { prepare, apply };
}
