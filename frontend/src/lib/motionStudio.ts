import { ApiError, sessionToken } from './api';

export interface MotionScene { ref: string; id: string; name: string; source: 'bundled' | 'project'; duration_ms: number; beats: number }
export interface MotionExportCapability { available: boolean; reason: string | null; license: string; format: 'webm'; codec: 'vp9'; mime: 'video/webm'; width: number; height: number; fps: number }
export interface MotionCatalogue { project: string; scenes: MotionScene[]; invalid: { file: string; error: string }[]; export: MotionExportCapability }
export interface MotionJob {
  id: string; project: string; scene: string; scene_id: string; name: string; reduced: boolean;
  phase: 'starting' | 'rendering' | 'encoding' | 'done' | 'failed' | 'cancelled';
  frame: number; frames: number; width: number; height: number; fps: number; created: number;
  finished?: number; error?: string; bytes?: number; seconds?: number; video_url?: string;
  format: 'webm'; codec: 'vp9'; encoder: { version: string; license: string };
}
export interface MotionExports { current: MotionJob | null; exports: MotionJob[] }
export const isRendering = (job: MotionJob | null) => !!job && ['starting', 'rendering', 'encoding'].includes(job.phase);
export const motionPath = (path: string, project: string) => '/api/runtime/motion/' + path + '?project=' + encodeURIComponent(project);

/** Fetch authenticated media; neither the iframe nor the video receives the room token. */
export async function motionMedia(path: string, signal?: AbortSignal): Promise<Response> {
  if (!path.startsWith('/api/runtime/motion/')) throw new Error('Unsupported motion media address');
  const token = sessionToken();
  const response = await fetch(path, { headers: token ? { 'X-Session-Token': token } : {}, signal, cache: 'no-store' });
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try { message = JSON.parse(text).error || message; } catch { /* Plain-text errors remain readable. */ }
    throw new ApiError(message, response.status);
  }
  return response;
}

export function previewTime(data: unknown, duration: number): { time: number; playing: boolean } | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.source !== 'zerocollab-motion' || d.version !== 1 || typeof d.time_ms !== 'number'
    || !Number.isFinite(d.time_ms) || typeof d.playing !== 'boolean') return null;
  return { time: Math.max(0, Math.min(duration, d.time_ms)), playing: d.playing };
}
