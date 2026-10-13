/* Audio plan (systems 6e90b5b7, seam pinned in b1e68bc7). Pure: everything systems' mixer needs, computed once
 * before any frame is drawn, with the same visibility and prop rules as the picture. The mixer owns synthesis and
 * PCM; this only says what sounds, when, and how loud on each composition frame. */
import { compositionProps, sampleField, type Composition, type SceneLayer } from './scene';
import { NO_PROPS, type Props } from './memo';

export const AUDIO_PLAN_VERSION = 1;
const AUDIBLE = new Set(['audio', 'video', 'voiceover']);

export interface AudioSound {
  id: string; type: 'audio' | 'video' | 'voiceover'; track: string;
  clip: { id: string; from: number; duration: number; offset: number };
  asset?: string; text?: string; voice?: string;
  /** In composition frames, like the picture's video trim. */
  trim: number; rate: number; role?: string; loop: boolean; duck?: unknown;
  /** Volume on local frames 0..duration (length duration + 1): the mixer interpolates PCM between samples. */
  volume: number[];
}
export interface AudioPlan { version: number; sounds: AudioSound[] }

const number = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;

/** Every audible layer: hidden or muted tracks, hidden layers and muted or silent video are left out, exactly as
 * the picture leaves out hidden layers; groups are walked. */
export function audioPlan(composition: Composition, overrides: Props = NO_PROPS): AudioPlan {
  const props = compositionProps(composition, overrides);
  const field = (layer: SceneLayer, name: string, frame = 0) => sampleField(layer, name, frame, props, composition.fps);
  const sounds: AudioSound[] = [];
  for (const track of composition.tracks) {
    if (track.hidden || track.muted) continue;
    for (const clip of track.clips) {
      const visit = (layers: readonly SceneLayer[]) => {
        for (const layer of layers) {
          if (layer.hidden) continue;
          if (layer.type === 'group') { visit(layer.layers ?? []); continue; }
          if (!AUDIBLE.has(layer.type)) continue;
          if (layer.type === 'video' && field(layer, 'muted') === true) continue;
          // Clamped to the mixer's [0, 1]: a spring-eased volume key may overshoot, which the picture allows (systems e14ff873).
          const volume = Array.from({ length: clip.duration + 1 }, (_, f) => Math.min(1, Math.max(0, number(field(layer, 'volume', Math.min(f, clip.duration - 1)), 1))));
          if (layer.type === 'video' && volume.every(v => v === 0)) continue;
          // speed retimes only the picture (timemap); a fixed-rate sound would drift from it, so it is refused here
          // as the schema refuses it (backend 0031), never mixed silently out of sync.
          if (layer.type === 'video' && ('speed' in layer || 'speed' in (layer.keyframes ?? {}))) {
            throw new RangeError(`video "${layer.id}" has speed, which its sound can't follow: set muted true`);
          }
          const sound: AudioSound = {
            id: layer.id, type: layer.type as AudioSound['type'], track: track.id,
            clip: { id: clip.id, from: clip.from, duration: clip.duration, offset: clip.offset ?? 0 },
            trim: number(field(layer, 'trim'), 0), rate: number(field(layer, 'rate'), 1),
            loop: field(layer, 'loop') === true, volume,
          };
          const optional = { asset: field(layer, 'asset'), text: field(layer, 'text'), voice: field(layer, 'voice'), role: field(layer, 'role'), duck: layer.duck };
          for (const [key, value] of Object.entries(optional)) if (value !== undefined) (sound as unknown as Record<string, unknown>)[key] = value;
          sounds.push(sound);
        }
      };
      visit(clip.layers);
    }
  }
  return { version: AUDIO_PLAN_VERSION, sounds };
}
