/** Scripted UI snapshots use clip-local integer frames. Resolving a frame never
 * reads or advances a store, so backward seeks and cold renders use the same data. */
export interface UiState<T> {
  readonly frame: number;
  readonly data: T;
}

export interface UiSnapshot<T> {
  readonly data: T;
  /** The immediately preceding authored snapshot, for deterministic layout endpoints. */
  readonly previous: T;
  readonly stateFrame: number;
  readonly elapsedFrames: number;
}

const MAX_UI_STATES = 120;
const MAX_SCENE_FRAME = 10 ** 7;

function integerFrame(value: number, name: string): void {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new RangeError(`${name} must be a non-negative safe integer`);
}

/** Validate once when a layer mounts. Content offsets can select a later part of
 * this timeline, so authored states are not limited to the visible clip duration. */
export function validateUiStates<T>(states: readonly UiState<T>[]): void {
  if (states.length > MAX_UI_STATES) throw new RangeError('UI layers support at most 120 states');
  let previous = 0;
  for (const state of states) {
    integerFrame(state.frame, 'state.frame');
    if (state.frame <= previous) throw new RangeError('UI states must have strictly increasing frames');
    if (state.frame > MAX_SCENE_FRAME) throw new RangeError('UI state frame exceeds the scene limit');
    previous = state.frame;
  }
}

/** Whole snapshots have step semantics. A state takes effect on its own frame.
 * Numeric motion uses elapsedFrames and the existing S1 evaluator separately. */
export function resolveUiState<T>(initial: T, states: readonly UiState<T>[], frame: number): UiSnapshot<T> {
  integerFrame(frame, 'frame');
  let low = 0, high = states.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (states[middle].frame <= frame) low = middle + 1;
    else high = middle;
  }
  const index = low - 1;
  const state = states[index];
  const stateFrame = state?.frame ?? 0;
  return {
    data: state ? state.data : initial,
    previous: index > 0 ? states[index - 1].data : initial,
    stateFrame,
    elapsedFrames: frame - stateFrame,
  };
}
