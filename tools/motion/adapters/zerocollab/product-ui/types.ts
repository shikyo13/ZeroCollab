import type { FrameContext, RationalFps } from '../../../motion/core/frame';
import type { ExecutionState, TaskStatus } from '../lib/types';
import type { UiState } from './timeline';

/** Scene vocabulary pinned by Backend 054e7148/59ffd60. Rendering maps these
 * bounded scripted records into the product's own props; they are not room state. */
export interface UiAgent {
  id: string;
  label: string;
  color: string;
}
export interface UiTask {
  id: string;
  title: string;
  status: TaskStatus;
  owner?: string;
  labels?: string[];
  priority?: number;
  next_action?: string;
  execution_state?: ExecutionState;
  updated?: number;
}
export interface UiFollow {
  agent: string;
  task: string;
  text?: string;
  tool?: string;
  open?: boolean;
  scroll?: number | 'end';
  move?: { from: TaskStatus; to: TaskStatus };
}
export interface UiData {
  epoch_seconds: number;
  project_title?: string;
  agents: UiAgent[];
  tasks: UiTask[];
  follow?: UiFollow[];
}
export interface UiLayer {
  type: 'ui';
  component: 'board' | 'task-card';
  label: 'scripted';
  data: UiData;
  states?: UiState<UiData>[];
  task?: string;
  width?: number;
  height?: number;
}

/** Systems a991b3f7 + Motion08e00534: global identity remains distinct from
 * clip-local presentation time, including an authored content offset. */
export interface UiContext {
  reduced?: boolean;
  frameContext: Readonly<FrameContext>;
  composition: { id: string; width: number; height: number; fps: RationalFps; duration: number };
  clip: { id: string; from: number; duration: number; frame: number; contentFrame: number };
  values: Record<string, unknown>;
}
export interface UiHandle {
  seek(context: UiContext): Promise<void>;
  dispose(): void;
}
