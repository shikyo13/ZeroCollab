import { finite, integer } from './math';

export interface RationalFps { num: number; den: number }
export interface FrameAsset {
  id: string; kind: 'image' | 'video' | 'audio' | 'font' | 'capture';
  sha256: string; ref: string; src: string;
}
export interface FrameInput {
  composition: { id: string; revision: string };
  fps: RationalFps;
  frame: number;
  seed: number;
  assets: readonly FrameAsset[];
}
export interface FrameContext extends FrameInput { seconds: number; milliseconds: number }

export function validateFps(fps: RationalFps): RationalFps {
  integer(fps.num, 'fps.num', 1); integer(fps.den, 'fps.den', 1);
  return fps;
}

export function frameToMilliseconds(frame: number, fps: RationalFps): number {
  integer(frame, 'frame'); validateFps(fps);
  return finite(frame * 1000 * fps.den / fps.num, 'frame time');
}

/** Realtime clocks enter at this boundary. Near an exact frame boundary, undo float roundoff. */
export function frameAtMilliseconds(milliseconds: number, fps: RationalFps): number {
  finite(milliseconds, 'milliseconds'); validateFps(fps);
  const value = Math.max(0, milliseconds) * fps.num / (1000 * fps.den);
  const nearest = Math.round(value);
  const frame = Math.abs(value - nearest) <= Number.EPSILON * 4 * Math.max(1, value) ? nearest : Math.floor(value);
  return integer(frame, 'frame', 0);
}

const ASSET_KINDS = ['image', 'video', 'audio', 'font', 'capture'];
// Safe asset refs: a room upload, a kit file, or a registered original used where it lies (backend FILEREF, systems
// 602d4e93: fileref:<16 lowercase hex>, never a font). The server resolves every ref and inlines the bytes as src.
const UPLOAD_REF = /^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/;
const KIT_REF = /^motion:(?=.{1,120}$)[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/;
const FILE_REF = /^fileref:[0-9a-f]{16}$/;
const FILEREF_KINDS = ['image', 'video', 'audio', 'capture'];
const safeRef = (asset: FrameAsset) => UPLOAD_REF.test(asset.ref) || KIT_REF.test(asset.ref)
  || (FILE_REF.test(asset.ref) && FILEREF_KINDS.includes(asset.kind));

/** Copies and freezes the exact identity inputs. Asset existence is checked by the scene service. */
export function frameContext(input: FrameInput): Readonly<FrameContext> {
  if (!input.composition?.id || !/^[\da-f]{64}$/i.test(input.composition.revision)) throw new RangeError('Composition id and SHA256 revision are required');
  integer(input.frame, 'frame', 0); integer(input.seed, 'seed', 0);
  if (input.seed > 0x7fffffff) throw new RangeError('seed must be in [0, 2^31-1]');
  const ids = new Set<string>();
  const assets = input.assets.map(asset => {
    if (!asset.id || ids.has(asset.id) || !/^[\da-f]{64}$/i.test(asset.sha256) || !safeRef(asset)
      || !ASSET_KINDS.includes(asset.kind)
      || typeof asset.src !== 'string' || !asset.src.startsWith('data:')) throw new RangeError('Assets need unique ids, SHA256 hashes, safe refs and server-inlined data sources');
    ids.add(asset.id);
    return Object.freeze({ ...asset });
  });
  const milliseconds = frameToMilliseconds(input.frame, input.fps);
  return Object.freeze({ composition: Object.freeze({ ...input.composition }), fps: Object.freeze({ ...input.fps }), frame: input.frame, seed: input.seed, assets: Object.freeze(assets), seconds: milliseconds / 1000, milliseconds });
}

export function evaluateFrame<T>(input: FrameInput, evaluate: (context: Readonly<FrameContext>) => T): T {
  return evaluate(frameContext(input));
}
