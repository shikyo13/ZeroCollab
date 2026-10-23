/** Product Board motion, shared by the interactive hook and frame renderer. */
export const BOARD_MOTION_CURVE = [0.2, 0, 0, 1] as const;
export const BOARD_MOTION_EASE = `cubic-bezier(${BOARD_MOTION_CURVE.join(',')})`;
export const BOARD_FLIGHT_MS = 560;
export const BOARD_REFLOW_MS = 320;
export const BOARD_CUE_MS = 1400;
export const BOARD_REDUCED_MS = 160;
export const BOARD_FLIGHT_OPACITY = { from: 0.9, to: 0.08 } as const;
export const BOARD_DONE_MIN_SCALE = 0.15;
export const BOARD_REDUCED_OPACITY = 0.6;
