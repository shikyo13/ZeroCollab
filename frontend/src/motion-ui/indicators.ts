import { easing } from '../../../motion/core/math';
import { frameToMilliseconds, type RationalFps } from '../../../motion/core/frame';
import { TASK_STATUSES, type TaskStatus } from '../lib/types';
import type { UiData, UiLayer } from './types';

// Product board.css: bd-pulse and bd-count-roll. The render bundle applies
// their curves directly, avoiding compositor animations and wall-clock starts.
const PULSE_MS = 1600;
const PULSE_MIN = 0.35;
const COUNT_MS = 260;
const COUNT_START_OPACITY = 0.3;
const COUNT_START_Y = -5;

function counts(data: UiData): Map<TaskStatus, number> {
  return new Map(TASK_STATUSES.map(status => [status, data.tasks.filter(task => task.status === status).length]));
}

export function indicatorClock(layer: UiLayer) {
  let previous = counts(layer.data);
  const changes = new Map(TASK_STATUSES.map(status => [status, [0]]));
  for (const state of layer.states ?? []) {
    const next = counts(state.data);
    for (const status of TASK_STATUSES)
      if (next.get(status) !== previous.get(status)) changes.get(status)!.push(state.frame);
    previous = next;
  }
  return (host: HTMLElement, frame: number, fps: RationalFps) => {
    const phase = (frameToMilliseconds(frame, fps) % PULSE_MS) / PULSE_MS;
    const pulse = easing['ease-in-out'](phase <= 0.5 ? phase * 2 : (1 - phase) * 2);
    host.style.setProperty('--ui-pulse-opacity', String(1 - (1 - PULSE_MIN) * pulse));
    for (const status of TASK_STATUSES) {
      const events = changes.get(status)!;
      let last = 0;
      for (const event of events) { if (event > frame) break; last = event; }
      const age = frameToMilliseconds(frame - last, fps);
      const progress = easing['ease-out'](Math.min(1, age / COUNT_MS));
      const counter = host.querySelector<HTMLElement>(`.bd-column-${status} .bd-count-roll`);
      counter?.style.setProperty('--ui-count-opacity', String(COUNT_START_OPACITY + (1 - COUNT_START_OPACITY) * progress));
      counter?.style.setProperty('--ui-count-y', `${COUNT_START_Y * (1 - progress)}px`);
    }
  };
}
