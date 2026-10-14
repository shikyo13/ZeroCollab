import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { frameToMilliseconds } from '../../../motion/core/frame';
import { BUBBLE_SIZE_MS } from '../components/SpeechBubble';
import { cardPositions, LayoutCache } from './layout';
import { boardMotion } from './boardMotion';
import { indicatorClock } from './indicators';
import { ProductBoard } from './ProductBoard';
import { UiPresentationContext } from './presentation';
import { resolveUiState, validateUiStates } from './timeline';
import type { UiContext, UiData, UiHandle, UiLayer } from './types';
import './ui.css';


async function mount(host: HTMLElement, incoming: UiLayer, initialContext: UiContext): Promise<UiHandle> {
  if (incoming.label !== 'scripted' || !['board', 'task-card'].includes(incoming.component))
    throw new Error('Unsupported or unlabeled UI layer');
  const layer: UiLayer = structuredClone(incoming);
  validateUiStates(layer.states ?? []);
  let node = document.createElement('div');
  node.className = 'ui-product modern-workspace';
  node.setAttribute('aria-label', 'Scripted product UI');
  host.append(node);
  let root = createRoot(node);
  let disposed = false;
  const layouts = new LayoutCache();
  const indicators = indicatorClock(layer);
  const motion = boardMotion(layer);
  const draw = (data: UiData, previous: UiData, ageMs: number) => {
    // Replace the render host too: retained Chromium clipping/raster layers can
    // otherwise carry a prior frame's edge pixels despite identical geometry.
    flushSync(() => root.unmount());
    const fresh = node.cloneNode(false) as HTMLDivElement;
    node.replaceWith(fresh); node = fresh; root = createRoot(node);
    const tree = <UiPresentationContext.Provider value={{ data, previous, ageMs }}>
      <ProductBoard layer={layer} data={data} />
    </UiPresentationContext.Provider>;
    flushSync(() => root.render(tree));
    // CardSpeech measures width in a passive effect in the app; flush pending
    // product measurements before reading endpoint geometry.
    flushSync(() => root.render(tree));
  };
  const seek = async (context: UiContext) => {
    if (disposed) throw new Error('UI handle was disposed');
    const { clip, composition } = context;
    const frame = clip.contentFrame;
    const snapshot = resolveUiState(layer.data, layer.states ?? [], frame);
    const ageMs = frameToMilliseconds(snapshot.elapsedFrames, composition.fps);
    const width = Number(context.values.width ?? layer.width ?? composition.width);
    const height = Number(context.values.height ?? layer.height ?? composition.height);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0)
      throw new Error('UI dimensions must be positive finite numbers');
    node.style.width = `${width}px`; node.style.height = `${height}px`;
    await document.fonts.ready;
    // Resolve all endpoint draws before drawing the captured frame. Each seek
    // constructs fresh DOM; ghost lifetime cannot depend on an earlier seek.
    const endpoints = new Map<number, { before: ReturnType<typeof cardPositions>; after: ReturnType<typeof cardPositions> }>();
    const prepare = (event: { frame: number; before: UiData; after: UiData }) => {
      const cached = endpoints.get(event.frame);
      if (cached) return cached;
      const read = (side: 'before' | 'after') => {
        const key = `${event.frame}:${side}:${width}:${height}`;
        let positions = layouts.get(key);
        if (!positions) {
          draw(event[side], event.before, side === 'before' ? BUBBLE_SIZE_MS : 0);
          positions = cardPositions(node); layouts.set(key, positions);
        }
        return positions;
      };
      const positions = { before: read('before'), after: read('after') };
      endpoints.set(event.frame, positions); return positions;
    };
    motion.prepare(frame, composition.fps, !!context.reduced, prepare);
    draw(snapshot.data, snapshot.previous, ageMs);
    motion.apply(node, frame, composition.fps, !!context.reduced, event => endpoints.get(event.frame)!);
    indicators(node, frame, composition.fps);
    await Promise.all(Array.from(node.querySelectorAll('img')).map(img => img.decode()));
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true; layouts.clear(); flushSync(() => root.unmount()); node.remove();
  };
  try { await seek(initialContext); } catch (error) { dispose(); throw error; }
  return { seek, dispose };
}

declare global { interface Window { ZCMotionUI: { mount: typeof mount } } }
window.ZCMotionUI = { mount };
