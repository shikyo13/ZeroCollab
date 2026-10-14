/*
  ZeroCollab motion kit: scene v2 renderer and page bootstrap (S5, Docs/motion-scene-v2.md).

  Loaded after core.js and motion.js on a page written by motion_studio.view_html() for a non-legacy v2
  scene. The page carries two JSON data blocks: zm-options { render, reduced, embed, t, frame_input,
  props? } and zm-scene (the validated v2 scene). frame_input selects the composition and carries the
  server-inlined data: assets, so the page never reaches the network or the disk.

  Every frame comes from ZCMotionCore.evaluate(composition, frame): a pure display list. This file only
  draws that list into DOM, so preview and export show the same pixels for the same frame.

  Renderer contract (read by the export worker):
    window.__scene        { version: 2, id, composition, fps, duration (frames), duration_ms, width, height }
    window.__seekFrame(n)  -> Promise<frame>, resolves once the frame's pixels are ready
    window.__seek(ms)      -> the same, for the frame at ms (clamped to the composition)
    window.__ready         true once fonts, images and videos have loaded and frame 0 (or opts.t) is drawn
    window.__error         plain text when the scene could not be built or a frame could not be drawn
*/
(function () {
  const CSS = `
    /* No will-change: a promoted layer would reuse raster tiles across transforms, so a frame's pixels would
       depend on the frames drawn before it. */
    .z2-clip { position: absolute; left: 0; top: 0; width: 100%; height: 100%; overflow: hidden; }
    .z2-off { visibility: hidden; }
    .z2-layer { position: absolute; left: 0; top: 0; box-sizing: border-box; }
    /* Unsized text takes its own width (wrapping only at max_width or newlines), wherever it is anchored. */
    .z2-text { width: max-content; white-space: pre-wrap; overflow-wrap: break-word; font-family: var(--zm-font); }
    .z2-captions { width: max-content; }
    .z2-rest { visibility: hidden; }
    .z2-media { overflow: hidden; }
    .z2-media > img, .z2-media > video { display: block; width: 100%; height: 100%; }
    /* Positioned on whole pixels (set in JS from the composition size), so their edges rasterise the same way
       on every frame. */
    .z2-badge { position: absolute; z-index: 9999; padding: 8px 18px; border-radius: 10px;
                font: 600 26px var(--zm-font); color: #1a1405; background: #f0c674; }
    .z2-strip { position: absolute; left: 0; right: 0; z-index: 9999; width: fit-content; margin: 0 auto;
                padding: 14px 28px; border-radius: 12px; font: 32px/1.3 var(--zm-font); color: var(--zm-text);
                background: #0c0e10e6; }
    .z2-captions { padding: 12px 26px; border-radius: 12px; white-space: pre-wrap; }
    .z2-svg { display: block; overflow: visible; }
    .z2-room { overflow: hidden; }
    .z2-room > .zm-stage { position: absolute; left: 0; top: 0; background: transparent; transform-origin: 0 0; }
    /* Camera (28cb5faf): one transform over every non-fixed clip. Fixed tracks and each clip's honesty HUD sit
       above it, so titles, lower-thirds, the Simulated badge and captions never move with the shot. */
    .z2-camera { position: absolute; left: 0; top: 0; width: 100%; height: 100%; transform-origin: 0 0; }
    .z2-hud { pointer-events: none; }
    .z2-ui { overflow: hidden; }
    /* Recorded pointer: drawn in its recording's box, so the recording's transform and the camera carry it. */
    .z2-cursor { position: absolute; left: 0; top: 0; z-index: 5; pointer-events: none; transform-origin: 0 0; }
    .z2-cursor svg { display: block; overflow: visible; filter: drop-shadow(0 2px 3px rgba(0, 0, 0, .55)); }
    .z2-ripple { position: absolute; left: 0; top: 0; border-radius: 50%; border: 3px solid var(--z2-ripple, #54d9ed); }
    /* Brand window: the app framed like a product shot instead of a screenshot with bars. */
    .z2-window { border-radius: 18px; overflow: hidden; background: #0c0e10;
                 box-shadow: 0 0 0 1px rgba(255, 255, 255, .09), 0 18px 40px rgba(0, 0, 0, .45), 0 50px 140px rgba(0, 0, 0, .55); }
    .z2-backdrop-paper { background: radial-gradient(130% 100% at 30% 10%, #f7f5f0 0%, #efece5 55%, #e6e2d9 100%) !important; }
    .z2-backdrop-paper .z2-window { box-shadow: 0 0 0 1px rgba(20, 24, 27, .10), 0 16px 34px rgba(20, 24, 27, .16), 0 44px 110px rgba(20, 24, 27, .20); }
    /* Content-sized plate: hugs the text it carries, so it never covers more of the shot than it needs. */
    .z2-plate { padding: 14px 26px 16px; border-radius: 16px; background: rgba(11, 15, 18, .9);
                box-shadow: 0 0 0 1px rgba(84, 217, 237, .18), 0 12px 32px rgba(0, 0, 0, .35); }
    /* On paper a caption or narration plate stays dark, so its light text and word highlight keep their contrast
       (design 3386aa7f); only its shadow softens to suit the light ground. */
    .z2-backdrop-paper .z2-plate:not(.z2-plate-accent) { box-shadow: 0 0 0 1px rgba(20, 24, 27, .10), 0 12px 32px rgba(20, 24, 27, .22); }
    .z2-plate-accent { padding: 8px 18px 9px; border-radius: 999px; background: #54d9ed; box-shadow: none; }
    /* Square, so the corners cover too; above its siblings whatever the layer order, within its own group only. */
    .z2-redact { z-index: 1; background: var(--z2-redact, #1b2023); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .06); }
    .z2-voice { position: absolute; left: 0; right: 0; width: fit-content; margin: 0 auto; max-width: 70%; text-align: center; }
    .z2-voice span { transition: none; }
    .z2-voice .z2-now { color: #8ee8f4; }
    .z2-word { display: inline-block; white-space: pre; will-change: auto; }
    .z2-backdrop-studio { background: radial-gradient(120% 90% at 22% 12%, #173037 0%, #10171a 42%, #0a0c0e 100%) !important; }
    .zm-transient { position: absolute; inset: 0; pointer-events: none; }
  `;
  const DEFAULT = {
    textSize: 48, textColor: '#e8ecee', lineHeight: 1.2, accent: '#54d9ed',
    shapeSize: 100, starInner: 0.5, lineWidth: 4,
    captionSize: 40, captionColor: '#ffffff', captionBackground: '#000000b3', captionBottom: 60, captionWidth: 0.8,
  };
  const LABEL = { inset: 0.04, stripWidth: 0.8 };   // capture badge and caption: safe-area inset, caption width
  const ROOM = { width: 1920, height: 1080, safe: 0.05, maxScale: 2.5, minMs: 1000, maxMs: 600000 };
  const SHRINK_STEPS = 18;            // binary search steps for fit: shrink (sub-pixel at any size)
  const SLIDE_ZOOM_FROM = 0.94;       // zoom transition starts at this scale
  const SVG = 'http://www.w3.org/2000/svg';
  // Recorded pointer: height px in the recording's box, the arrow's drawing box, and a click ripple that opens from
  // `from` to from + grow pointer heights over rippleS, fading from `alpha`; hotspot is the arrow tip's inset px.
  const CURSOR = { size: 30, box: [20, 26], rippleS: 0.5, ripple: { from: 0.4, grow: 1.6, alpha: 0.7, hotspot: 2 } };
  // Redaction is always an opaque cover: a blur can leave text partly recoverable and reads as a glitch on screen.
  // Its own opacity, blur and colour alpha are never applied (systems e5552333); effects on its group or the camera
  // act on the already-covered footage, so they can't uncover it.
  const REDACT = { color: '#1b2023', opaque: { opacity: 1, blur: 0 } };
  // Voice captions (b1e68bc7): pages of about two lines, held briefly after the last word.
  const VOICE_CAPTION = { pageChars: 84, holdS: 0.6, bottom: 0.07, upcoming: 0.5 };
  // 'ui' blur (design 14994): a hint of motion, never defocus. It is zero once the camera lands, because it comes
  // from frame-to-frame camera motion only.
  const BLUR = { shutter: 180, sigma: 3, max: 2.5, min: 0.35 };   // degrees; box-to-Gaussian ratio; px caps
  // ZeroCollab brand text styles (28cb5faf): sizes on a 96/64/40/28/20 scale, colours from the app's tokens.
  const TEXT_STYLES = {
    // Weights follow design's Inter decision: 800 short hero lines, 600 captions and kickers, 400 body.
    display: { size: 140, weight: 800, line_height: 1.0, letter_spacing: -3, color: '#eef2f3' },
    title: { size: 96, weight: 800, line_height: 1.05, letter_spacing: -1.5, color: '#eef2f3' },
    headline: { size: 64, weight: 800, line_height: 1.1, letter_spacing: -0.8, color: '#eef2f3' },
    subtitle: { size: 40, weight: 400, line_height: 1.25, color: '#8ee8f4' },
    kicker: { size: 24, weight: 600, line_height: 1.2, letter_spacing: 3, color: '#54d9ed', uppercase: true },
    'lt-name': { size: 40, weight: 600, line_height: 1.15, color: '#eef2f3' },
    'lt-role': { size: 28, weight: 400, line_height: 1.3, color: '#aab5ba' },
    // A caption carries its own plate, sized to its text (design: no fixed full-width panel over every shot).
    caption: { size: 36, weight: 600, line_height: 1.3, color: '#eef2f3', plate: true },
    // A small accent pill for labels such as "Time-lapse x8" or "Example project".
    badge: { size: 22, weight: 600, line_height: 1.2, letter_spacing: 2, color: '#071518', uppercase: true, plate: 'accent' },
  };
  // On the light paper backdrop, brand styles switch to ink so contrast holds (captions keep their dark plate).
  const PAPER_INK = { display: '#121517', title: '#121517', headline: '#121517', subtitle: '#56626b', kicker: '#0b7f90',
                      'lt-name': '#121517', 'lt-role': '#56626b' };
  // Word stagger: each word rises this far and fades in over this many frames, a new word every `every` frames.
  const STAGGER = { rise: 26, frames: 14, every: 4 };
  // Clip stacking: picture clips count up from 1; every honesty HUD sits above all of them.
  const HUD_Z = 1000;
  const familyOf = id => 'zm-' + id.replace(/-w\d{3}$/, '');

  const core = window.ZCMotionCore, kit = window.ZCMotion;
  const fail = err => {
    const text = 'Scene failed: ' + (err && err.message || err);
    if (!document.querySelector('.zm-error')) {
      const box = document.createElement('div');
      box.className = 'zm-error';
      box.textContent = text;
      document.body.append(box);
    }
    window.__error = text;
  };
  const data = id => {
    const block = document.getElementById(id);
    return block ? JSON.parse(block.textContent) : null;
  };
  const num = (v, fallback) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;
  const str = (v, fallback) => typeof v === 'string' && v ? v : fallback;
  const px = v => v + 'px';
  // Scene colours are #rgb, #rgba, #rrggbb or #rrggbbaa; dropping the alpha digits leaves the opaque colour.
  const opaqueColor = c => {
    const color = str(c, REDACT.color);
    return color.length === 5 || color.length === 9 ? color.slice(0, color.length === 5 ? 4 : 7) : color;
  };
  const twoFrames = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  function decodeDataUrl(src) {
    const comma = src.indexOf(',');
    const head = src.slice(0, comma), body = src.slice(comma + 1);
    if (!/;base64$/i.test(head)) return new TextEncoder().encode(decodeURIComponent(body));
    const bin = atob(body), bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  function create(scene, opts) {
    if (!core) throw new Error('motion core (core.js) is not loaded');
    const input = opts.frame_input;
    if (!input) throw new Error('a v2 scene needs frame_input');
    const comp = (scene.compositions || []).find(c => c.id === input.composition.id);
    if (!comp) throw new Error(`composition "${input.composition.id}" is not in the scene`);
    const reduced = opts.reduced === true;
    const props = opts.props && typeof opts.props === 'object' ? opts.props : core.NO_PROPS;
    // The composition's defaults under these overrides: the one props object the frame, footage clocks, camera and
    // sound plan all read (core caches by its identity).
    const merged = core.compositionProps(comp, props);
    // The identity check (revision, seed, asset hashes and safe refs) runs once here and on every frame.
    const assets = new Map(core.frameContext({ ...input, frame: 0 }).assets.map(a => [a.id, a]));
    const asset = (id, kinds) => {
      const a = assets.get(id);
      if (!a) throw new Error(`asset "${id}" was not provided`);
      if (!kinds.includes(a.kind)) throw new Error(`asset "${id}" is a ${a.kind}, not ${kinds.join(' or ')}`);
      return a;
    };
    const fps = comp.fps, W = comp.width, H = comp.height;
    const durationMs = core.frameToMilliseconds(comp.duration, fps);

    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.append(style);
    const frame = document.getElementById('frame'), stage = document.getElementById('stage');
    for (const el of [frame, stage]) Object.assign(el.style, { width: px(W), height: px(H) });
    stage.style.background = str(comp.background, '');
    if (comp.backdrop === 'studio' || comp.backdrop === 'paper') stage.classList.add('z2-backdrop-' + comp.backdrop);
    const camera = document.createElement('div');
    camera.className = 'z2-camera';
    stage.append(camera);
    // Velocity motion blur: one directional Gaussian on the camera layer, sized from the camera's own motion
    // between frames n-1 and n (never playback history). Edges duplicate, so the frame border never darkens.
    const shutter = comp.motion_blur ? num(comp.motion_blur.shutter, BLUR.shutter) : 0;
    let blurNode = null;
    if (shutter > 0) {
      const svg = document.createElementNS(SVG, 'svg');
      svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.style.position = 'absolute';
      svg.innerHTML = '<filter id="z2-mblur" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="0 0" edgeMode="duplicate"/></filter>';
      stage.append(svg);
      blurNode = svg.querySelector('feGaussianBlur');
    }

    const fontLoads = [];
    for (const a of assets.values()) {
      if (a.kind !== 'font') continue;
      // Bytes, not a URL: the face never fetches, so the page needs no font-src.
      // An id ending in -w400, -w600 ... is that weight of one family: zc-sans-w700 is "zm-zc-sans" at 700.
      const weight = /-w(\d{3})$/.exec(a.id);
      const face = new FontFace(familyOf(a.id), decodeDataUrl(a.src), weight ? { weight: weight[1] } : {});
      document.fonts.add(face);
      fontLoads.push(face.load());
    }

    const nodes = new Map();    // layer id -> { el, type, ... }
    const clips = new Map();    // clip id -> { el, clip }
    const rooms = [];           // room layers, to fit once fonts have loaded
    const media = [];           // img and video elements to wait for
    const cursors = [];         // cursor layers, moved into their source video's box once the clip is built
    const uiMounts = [], uiHandles = [];   // ui layers: mounts to await before the first frame, handles to dispose
    // The one context envelope a ui layer sees (frontend 08e00534, systems a991b3f7): the S1 frame identity, the
    // composition, the clip (UI time is clip.contentFrame at composition.fps) and the layer's resolved values.
    const contextFor = (clip, frameNumber, contentFrame, values) => ({
      frameContext: core.frameContext({ ...input, frame: frameNumber }),
      composition: { id: comp.id, width: W, height: H, fps, duration: comp.duration },
      clip: { id: clip.id, from: clip.from, duration: clip.duration, frame: frameNumber - clip.from, contentFrame },
      values,
      // The page's reduced-motion request (zm-options.reduced), so a ui layer can draw the product's reduced variant
      // (frontend 3a78adb5).
      reduced,
    });
    addEventListener('pagehide', () => { for (const h of uiHandles.splice(0)) try { h.dispose(); } catch (e) { /* teardown only */ } });

    function buildLayer(layer, parent, clip) {
      const el = document.createElement('div');
      el.className = 'z2-layer z2-' + layer.type;
      el.dataset.layer = layer.id;
      parent.append(el);
      const node = { el, type: layer.type, layer, clip };
      switch (layer.type) {
        case 'text': break;
        case 'image': case 'capture': {
          el.classList.add('z2-media');
          const img = document.createElement('img');
          img.alt = '';
          img.src = asset(layer.asset, ['image', 'capture']).src;
          el.append(img);
          media.push(img);
          node.img = img;
          break;
        }
        case 'video': {
          el.classList.add('z2-media');
          const video = document.createElement('video');
          video.muted = true;           // sound is the exporter's mix, never the page's
          video.playsInline = true;
          video.preload = 'auto';
          video.src = asset(layer.asset, ['video']).src;
          el.append(video);
          media.push(video);
          node.video = video;
          break;
        }
        case 'shape': case 'path': {
          const svg = document.createElementNS(SVG, 'svg');
          svg.setAttribute('class', 'z2-svg');
          node.svg = svg;
          node.shape = document.createElementNS(SVG, 'path');   // every shape is drawn as path data
          svg.append(node.shape);
          el.append(svg);
          break;
        }
        case 'captions': el.classList.add('z2-captions'); break;
        case 'redact': el.classList.add('z2-redact'); break;
        case 'group': for (const child of layer.layers || []) if (!child.hidden) buildLayer(child, el, clip); break;
        case 'room': buildRoom(node, clip); break;
        case 'ui': {
          // Real ZeroCollab components, mounted by frontend's motion/ui.js with scripted state. Each seek must be a
          // pure function of its context; render2 awaits it like a video seek.
          const ui = window.ZCMotionUI;
          if (!ui || typeof ui.mount !== 'function') throw new Error(`ui layer "${layer.id}" needs motion/ui.js`);
          el.classList.add('z2-ui');
          const mounting = Promise.resolve(ui.mount(el, layer, contextFor(clip, clip.from, 0, {})));
          uiMounts.push(mounting.then(handle => { node.handle = handle; uiHandles.push(handle); }));
          break;
        }
        case 'cursor': {
          if (!Array.isArray(layer.viewport) || !layer.fps) throw new Error(`cursor "${layer.id}" needs viewport and fps from its recording`);
          node.ripple = document.createElement('div');
          node.ripple.className = 'z2-ripple';
          const svg = document.createElementNS(SVG, 'svg');
          svg.setAttribute('viewBox', `0 0 ${CURSOR.box.join(' ')}`);
          svg.innerHTML = '<path d="M2 2 L2 21.5 L7.2 16.6 L10.9 24.6 L14.3 23.1 L10.7 15.2 L17.6 15.2 Z" fill="#fff" stroke="#0c0e10" stroke-width="1.5" stroke-linejoin="round"/>';
          node.svg = svg;
          el.className = 'z2-cursor';
          el.append(node.ripple, svg);
          node.track = core.cursorTrack(layer.events || [], layer.fps);
          cursors.push(node);
          break;
        }
        default: throw new Error(`layer "${layer.id}" has unknown type "${layer.type}"`);
      }
      nodes.set(layer.id, node);
    }

    // A v1 room scene (agents, board, browser, cards, beats) drawn by motion.js on its own 1920x1080
    // stage, then scaled so its content fills the composition's safe area. Handoff chips are transient
    // here: they fade once the receiver has the work, which v1 pages never do.
    function buildRoom(node, clip) {
      if (!kit) throw new Error('room layers need motion.js');
      node.el.classList.add('z2-room');
      Object.assign(node.el.style, { width: px(W), height: px(H) });
      const host = document.createElement('div');
      host.className = 'zm-stage';
      node.el.append(host);
      const exact = Math.ceil(core.frameToMilliseconds(clip.duration, fps));
      node.room = kit.create(host, { duration: Math.max(ROOM.minMs, Math.min(ROOM.maxMs, exact)), reduced, transient: true });
      node.room.play(node.layer.scene.beats || [], kit.layout(node.room, node.layer.scene.layout || {}));
      node.host = host;
      rooms.push(node);
    }

    function fitRoom(node) {
      // The union of everything the room laid out (arrows span the whole stage, so they are left out).
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const child of node.host.querySelectorAll('.zm-title, .zm-agent, .zm-board, .zm-browser, .zm-arrow-label, .zm-emphasis-label, .zm-caption')) {
        const p = node.room.pos(child);
        if (!p.w || !p.h) continue;
        x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + p.w); y1 = Math.max(y1, p.y + p.h);
      }
      if (!(x1 > x0 && y1 > y0)) return;
      const k = Math.min(W * (1 - 2 * ROOM.safe) / (x1 - x0), H * (1 - 2 * ROOM.safe) / (y1 - y0), ROOM.maxScale);
      const tx = (W - (x1 - x0) * k) / 2 - x0 * k, ty = (H - (y1 - y0) * k) / 2 - y0 * k;
      node.host.style.transform = `translate(${tx}px, ${ty}px) scale(${k})`;
    }

    for (const track of comp.tracks) {
      if (track.hidden || track.kind !== 'visual') continue;
      for (const clip of track.clips) {
        const el = document.createElement('div');
        el.className = 'z2-clip z2-off';
        el.dataset.clip = clip.id;
        (track.fixed ? stage : camera).append(el);
        for (const layer of clip.layers) if (!layer.hidden) buildLayer(layer, el, clip);
        for (const cursor of cursors.splice(0)) {
          const source = nodes.get(cursor.layer.source);
          if (!source || source.type !== 'video') throw new Error(`cursor "${cursor.layer.id}" needs its source video in the same clip`);
          source.el.append(cursor.el);
          cursor.source = source;
        }
        // A capture's honesty label and caption belong to the frame, not the picture: neither a layer zoom nor
        // the camera may scale them out of view. They sit in the clip's own HUD, above the camera.
        const hud = document.createElement('div');
        hud.className = 'z2-clip z2-hud z2-off';
        stage.append(hud);
        const label = (cls, place) => { const n = document.createElement('div'); n.className = cls; Object.assign(n.style, place); hud.append(n); return n; };
        clips.set(clip.id, { el, hud, clip,
          badge: label('z2-badge', { left: px(Math.round(W * LABEL.inset)), top: px(Math.round(H * LABEL.inset)) }),
          strip: label('z2-strip', { bottom: px(Math.round(H * LABEL.inset)), maxWidth: px(Math.round(W * LABEL.stripWidth)) }) });
      }
    }

    // Voice captions: one fixed HUD caption per narrated voiceover layer whose captions are enabled and whose timing
    // sidecar the server inlined (zm-options.voice_captions). 'word' highlights the spoken word; 'phrase' times whole
    // pages from the first and last word only; 'none' draws nothing, so no timing is ever invented.
    const voices = [];
    for (const track of comp.tracks) {
      if (track.hidden || track.muted || track.kind !== 'audio') continue;
      for (const clip of track.clips) for (const layer of clip.layers) {
        const sidecar = opts.voice_captions && opts.voice_captions[layer.id];
        if (layer.type !== 'voiceover' || layer.hidden || !sidecar || (layer.captions && layer.captions.enabled === false)) continue;
        if (!['word', 'phrase'].includes(sidecar.timing_quality) || !Array.isArray(sidecar.words) || !sidecar.words.length) continue;
        const el = document.createElement('div');
        el.className = 'z2-voice z2-text z2-plate';
        const cap = TEXT_STYLES.caption;   // the brand caption style, so narration reads like every other caption
        Object.assign(el.style, { bottom: px(Math.round(H * VOICE_CAPTION.bottom)), fontFamily: fontFamily({}), fontSize: px(cap.size),
          fontWeight: String(cap.weight), lineHeight: String(cap.line_height), color: cap.color });
        stage.append(el);
        voices.push({ clip, sidecar, el, pages: pagesOf(sidecar.words) });
      }
    }

    function pagesOf(words) {
      const pages = [];
      let page = [], chars = 0;
      for (const w of words) {
        if (page.length && chars + w.text.length + 1 > VOICE_CAPTION.pageChars) { pages.push(page); page = []; chars = 0; }
        page.push(w); chars += w.text.length + 1;
        if (/[.!?]$/.test(w.text)) { pages.push(page); page = []; chars = 0; }   // a sentence end turns the page
      }
      if (page.length) pages.push(page);
      return pages;
    }

    function drawVoices(n) {
      for (const voice of voices) {
        const { clip, sidecar, el, pages } = voice, local = n - clip.from;
        const sample = (local + (clip.offset || 0)) * fps.den / fps.num * sidecar.sample_rate;
        const hold = VOICE_CAPTION.holdS * sidecar.sample_rate;
        let current = -1;
        pages.forEach((page, i) => { if (page[0].start_sample <= sample) current = i; });
        const page = pages[current];
        const showing = local >= 0 && local < clip.duration && page && sample <= page[page.length - 1].end_sample + hold;
        el.style.display = showing ? '' : 'none';
        if (!showing) continue;
        if (voice.shown !== current) {
          voice.shown = current;
          el.textContent = '';
          voice.spans = page.map((w, i) => { const span = document.createElement('span'); span.textContent = (i ? ' ' : '') + w.text; el.append(span); return span; });
        }
        if (sidecar.timing_quality !== 'word') continue;
        page.forEach((w, i) => {
          const span = voice.spans[i];
          span.classList.toggle('z2-now', w.start_sample <= sample && sample < w.end_sample);
          span.style.opacity = sample < w.start_sample ? String(VOICE_CAPTION.upcoming) : '1';
        });
      }
    }

    // ---- drawing one frame ----

    function transform(node, v, defaults = {}) {
      const el = node.el;
      const ax = num(v.anchor_x, defaults.anchor_x ?? 0), ay = num(v.anchor_y, defaults.anchor_y ?? 0);
      const s = num(v.scale, 1), sx = s * num(v.scale_x, 1), sy = s * num(v.scale_y, 1);
      el.style.left = px(num(v.x, defaults.x ?? 0));
      el.style.top = px(num(v.y, defaults.y ?? 0));
      const w = num(v.width, defaults.width), h = num(v.height, defaults.height);
      el.style.width = w === undefined ? '' : px(w);
      el.style.height = h === undefined ? '' : px(h);
      el.style.transformOrigin = `${ax * 100}% ${ay * 100}%`;
      el.style.transform = `translate(${-ax * 100}%, ${-ay * 100}%) rotate(${num(v.rotate, 0)}deg) scale(${sx}, ${sy})`;
      el.style.opacity = String(num(v.opacity, 1));
      const blur = num(v.blur, 0);
      el.style.filter = blur ? `blur(${blur}px)` : '';
    }

    function fontFamily(v) {
      const fa = v.font_asset || (!v.font && comp.font_asset);
      if (fa) return `"${familyOf(asset(fa, ['font']).id)}", var(--zm-font)`;
      return v.font ? `"${String(v.font).replace(/["\\]/g, '')}", var(--zm-font)` : '';
    }

    function drawText(node, given) {
      // A brand style supplies defaults; anything the layer sets itself wins.
      const preset = given.style && TEXT_STYLES[given.style];
      const ink = comp.backdrop === 'paper' && PAPER_INK[given.style] ? { color: PAPER_INK[given.style] } : {};
      const v = preset ? { ...preset, ...ink, ...given } : given;
      if (v.stagger) return drawStagger(node, v, given);
      const el = node.el, text = String(v.text ?? '');
      const reveal = Math.max(0, Math.min(1, num(v.reveal, 1)));
      const chars = Array.from(text), shown = Math.round(chars.length * reveal);
      const key = text + '\u0000' + shown;
      if (node.key !== key) {
        node.key = key;
        el.textContent = '';
        el.append(chars.slice(0, shown).join(''));
        // The unrevealed rest keeps its space, so a typing reveal never re-wraps the lines already shown.
        if (shown < chars.length) {
          const rest = document.createElement('span');
          rest.className = 'z2-rest';
          rest.textContent = chars.slice(shown).join('');
          el.append(rest);
        }
      }
      Object.assign(el.style, {
        fontFamily: fontFamily(v), fontWeight: String(num(v.weight, 400)), color: str(v.color, DEFAULT.textColor),
        textAlign: str(v.align, 'left'), lineHeight: String(num(v.line_height, DEFAULT.lineHeight)),
        letterSpacing: px(num(v.letter_spacing, 0)), maxWidth: v.max_width === undefined ? '' : px(num(v.max_width, 0)),
        textTransform: v.uppercase ? 'uppercase' : '', textShadow: v.shadow ? '0 2px 18px rgba(0, 0, 0, .55)' : '',
      });
      el.classList.toggle('z2-plate', !!v.plate);
      el.classList.toggle('z2-plate-accent', v.plate === 'accent');
      const size = num(v.size, DEFAULT.textSize);
      el.style.fontSize = px(size);
      if (v.fit !== 'shrink') return;
      // Largest size up to `size` at which the text fits its box (width or max_width, and height if set).
      const boxW = num(v.width, num(v.max_width, undefined)), boxH = num(v.height, undefined);
      if (boxW === undefined && boxH === undefined) return;
      const fits = () => (boxW === undefined || el.scrollWidth <= Math.ceil(boxW)) && (boxH === undefined || el.scrollHeight <= Math.ceil(boxH));
      if (fits()) return;
      let low = 1, high = size;
      for (let i = 0; i < SHRINK_STEPS; i++) {
        const mid = (low + high) / 2;
        el.style.fontSize = px(mid);
        if (fits()) low = mid; else high = mid;
      }
      el.style.fontSize = px(low);
    }

    // The pointer at the recording's own time (the same clock seekVideo uses), mapped from viewport px into the
    // video's box. Its own x and y nudge it, scale sizes it, opacity fades it.
    function drawCursor(node, v, clipDraw) {
      let sv = {};
      walk(clipDraw.layers, item => { if (item.id === node.layer.source) sv = item.values; });
      const seconds = sourceSeconds(node.source.layer, clipDraw, clipDraw.contentFrame);
      const at = node.track(seconds), k = num(sv.width, W) / node.layer.viewport[0];
      const size = num(v.size, CURSOR.size) * num(v.scale, 1);
      node.el.style.display = at.visible ? '' : 'none';
      node.el.style.opacity = String(num(v.opacity, 1));
      node.el.style.transform = `translate(${at.x * k + num(v.x, 0)}px, ${at.y * k + num(v.y, 0)}px)`;
      node.svg.setAttribute('width', String(size * CURSOR.box[0] / CURSOR.box[1])); node.svg.setAttribute('height', String(size));
      const r = node.ripple;
      if (at.clickAge === null) { r.style.display = 'none'; return; }
      // A ring that opens and fades over half a second, centred on the hotspot.
      const ring = CURSOR.ripple, p = at.clickAge / CURSOR.rippleS, e = core.easing['expo-out'](p), d = size * (ring.from + ring.grow * e);
      const edge = px(-d / 2 + ring.hotspot);
      Object.assign(r.style, { display: '', width: px(d), height: px(d), left: edge, top: edge, opacity: String(ring.alpha * (1 - p)) });
      if (v.color) r.style.setProperty('--z2-ripple', v.color);
    }

    // Kinetic type: words enter one after another from v.stagger.from (clip frame), every v.stagger.frames apart,
    // each rising and fading in with expo-out. The text keeps its final layout, so nothing re-wraps mid-move.
    function drawStagger(node, v, given) {
      const el = node.el, words = String(v.text ?? '').split(/(\s+)/);
      if (node.key !== 'stagger:' + v.text) {
        node.key = 'stagger:' + v.text;
        el.textContent = '';
        node.words = words.map(w => { const span = document.createElement('span'); span.className = 'z2-word'; span.textContent = w; el.append(span); return span; });
      }
      Object.assign(el.style, {
        fontFamily: fontFamily(v), fontWeight: String(num(v.weight, 400)), color: str(v.color, DEFAULT.textColor),
        textAlign: str(v.align, 'left'), lineHeight: String(num(v.line_height, DEFAULT.lineHeight)),
        letterSpacing: px(num(v.letter_spacing, 0)), fontSize: px(num(v.size, DEFAULT.textSize)),
        maxWidth: v.max_width === undefined ? '' : px(num(v.max_width, 0)),
      });
      const st = v.stagger, from = num(st.from, 0), every = num(st.every, STAGGER.every), dur = num(st.frames, STAGGER.frames);
      const local = node.clipFrame ?? 0, ease = core.easing['expo-out'];
      let k = 0;
      node.words.forEach((span, i) => {
        if (!words[i].trim()) return;
        const p = Math.max(0, Math.min(1, (local - from - k * every) / dur)), e = ease(p);
        span.style.opacity = String(e);
        span.style.transform = `translateY(${(1 - e) * num(st.rise, STAGGER.rise)}px)`;
        k++;
      });
    }

    function polygon(sides, w, h, inner) {
      const pts = [], n = inner === undefined ? sides : sides * 2;
      for (let i = 0; i < n; i++) {
        const a = -Math.PI / 2 + i * 2 * Math.PI / n, r = inner !== undefined && i % 2 ? inner : 1;
        pts.push(`${w / 2 + Math.cos(a) * r * w / 2} ${h / 2 + Math.sin(a) * r * h / 2}`);
      }
      return 'M ' + pts.join(' L ') + ' Z';
    }

    function drawShape(node, v) {
      const w = num(v.width, DEFAULT.shapeSize), h = num(v.height, DEFAULT.shapeSize);
      node.svg.setAttribute('width', w); node.svg.setAttribute('height', h);
      const kind = v.shape, r = Math.min(num(v.radius, 0), w / 2, h / 2);
      let d;
      if (kind === 'rect') d = r ? `M ${r} 0 H ${w - r} A ${r} ${r} 0 0 1 ${w} ${r} V ${h - r} A ${r} ${r} 0 0 1 ${w - r} ${h} H ${r} A ${r} ${r} 0 0 1 0 ${h - r} V ${r} A ${r} ${r} 0 0 1 ${r} 0 Z` : `M 0 0 H ${w} V ${h} H 0 Z`;
      else if (kind === 'ellipse') d = `M 0 ${h / 2} A ${w / 2} ${h / 2} 0 1 0 ${w} ${h / 2} A ${w / 2} ${h / 2} 0 1 0 0 ${h / 2} Z`;
      else if (kind === 'line' || (kind === 'polygon' && Array.isArray(v.points))) {
        const pts = (v.points || [[0, h / 2], [w, h / 2]]).map(p => `${p[0]} ${p[1]}`);
        d = 'M ' + pts.join(' L ') + (kind === 'polygon' ? ' Z' : '');
      } else if (kind === 'polygon') d = polygon(num(v.sides, 6), w, h);
      else if (kind === 'star') d = polygon(num(v.sides, 5), w, h, num(v.inner_radius, DEFAULT.starInner));
      else throw new Error(`shape layer "${node.layer.id}" has unknown shape "${kind}"`);
      const line = kind === 'line';
      paint(node.shape, d, v.fill === undefined ? (line || v.stroke ? 'none' : DEFAULT.accent) : v.fill,
        v.stroke === undefined ? (line ? DEFAULT.accent : 'none') : v.stroke, num(v.stroke_width, line ? DEFAULT.lineWidth : 0));
    }

    function drawPath(node, v) {
      node.svg.setAttribute('width', num(v.width, W)); node.svg.setAttribute('height', num(v.height, H));
      paint(node.shape, String(v.d), v.fill === undefined ? 'none' : v.fill, v.stroke === undefined ? DEFAULT.accent : v.stroke, num(v.stroke_width, DEFAULT.lineWidth));
      // Drawn on by progress: a unit path length, dashed to the visible fraction.
      const progress = Math.max(0, Math.min(1, num(v.progress, 1)));
      node.shape.setAttribute('pathLength', '1');
      node.shape.setAttribute('stroke-dasharray', '1 1');
      node.shape.setAttribute('stroke-dashoffset', String(1 - progress));
    }

    function paint(shape, d, fill, stroke, width) {
      shape.setAttribute('d', d);
      shape.setAttribute('fill', fill);
      shape.setAttribute('stroke', stroke);
      shape.setAttribute('stroke-width', width);
      shape.setAttribute('stroke-linecap', 'round');
      shape.setAttribute('stroke-linejoin', 'round');
    }

    function drawCaptions(node, v, local) {
      const cue = (v.cues || []).find(c => local >= c.from && local < c.from + c.duration);
      node.el.textContent = cue ? cue.text : '';
      node.el.style.display = cue ? '' : 'none';
      Object.assign(node.el.style, {
        fontFamily: fontFamily(v), fontSize: px(num(v.size, DEFAULT.captionSize)), color: str(v.color, DEFAULT.captionColor),
        background: str(v.background, DEFAULT.captionBackground), textAlign: str(v.align, 'center'),
        maxWidth: px(W * DEFAULT.captionWidth),
      });
    }

    // One clock for recorded footage: the video, its cursor and the follow camera all read core.timeMap
    // (trim, rate and keyframed speed ramps), so a ramp never desynchronises the pointer from the picture.
    function sourceSeconds(layer, clipDraw, contentFrame) {
      const frames = clipDraw.duration + (clipDraw.contentFrame - clipDraw.frame);
      return core.timeMap(layer, frames, merged, fps).seconds(contentFrame);
    }

    const waits = [];
    function seekVideo(node, v, clipDraw) {
      const seconds = sourceSeconds(node.layer, clipDraw, clipDraw.contentFrame);
      const video = node.video, target = Math.min(seconds, Math.max(0, (video.duration || seconds) - 1e-3));
      if (Math.abs(video.currentTime - target) < 1e-4) return;
      waits.push(new Promise((resolve, reject) => {
        const done = () => { video.removeEventListener('seeked', done); video.removeEventListener('error', bad); resolve(); };
        const bad = () => { video.removeEventListener('seeked', done); video.removeEventListener('error', bad); reject(new Error(`video "${node.layer.id}" could not seek`)); };
        video.addEventListener('seeked', done);
        video.addEventListener('error', bad);
        video.currentTime = target;
      }));
    }

    function drawLayer(item, clipDraw) {
      const node = nodes.get(item.id), v = item.values;
      if (!node) return;
      switch (item.type) {
        case 'text': transform(node, v); node.clipFrame = clipDraw.frame; drawText(node, v); break;
        case 'image': case 'capture': case 'video':
          transform(node, v, { width: W, height: H });
          node.el.classList.toggle('z2-window', v.frame === 'window');
          (node.img || node.video).style.objectFit = str(v.fit, 'contain');
          if (node.video) seekVideo(node, v, clipDraw);
          break;
        case 'shape': transform(node, v); drawShape(node, v); break;
        case 'path': transform(node, v); drawPath(node, v); break;
        case 'captions':
          transform(node, v, { x: W / 2, y: H - DEFAULT.captionBottom, anchor_x: 0.5, anchor_y: 1 });
          drawCaptions(node, v, clipDraw.frame);
          break;
        case 'group':
          transform(node, v);
          // A group with a size is a viewport: zoom and pan a capture inside it without leaving its box.
          node.el.style.overflow = v.width !== undefined && v.height !== undefined ? 'hidden' : '';
          node.el.classList.toggle('z2-window', v.frame === 'window');
          for (const child of item.children || []) drawLayer(child, clipDraw);
          break;
        case 'room':
          node.room.seek(core.frameToMilliseconds(clipDraw.contentFrame, fps));
          break;
        case 'cursor': drawCursor(node, v, clipDraw); break;
        case 'redact':
          // Covers keys, emails or account names inside footage; it sits in the footage's own group, so it moves
          // and zooms with it.
          transform(node, { ...v, ...REDACT.opaque }, { width: 0, height: 0 });
          node.el.style.setProperty('--z2-redact', opaqueColor(v.color));
          break;
        case 'ui':
          transform(node, v, { width: W, height: H });
          node.el.classList.toggle('z2-window', v.frame === 'window');
          if (!node.handle) throw new Error(`ui layer "${node.layer.id}" was not mounted`);
          waits.push(Promise.resolve(node.handle.seek(contextFor(clips.get(clipDraw.id).clip, frameNow, clipDraw.contentFrame, v))));
          break;
      }
    }

    // Directions are the direction of travel: a slide left moves the new clip in from the right and pushes
    // the old one out to the left; a wipe up uncovers the new clip from the bottom edge upwards.
    const AXIS = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] };
    const WIPE_IN = { left: p => `0 0 0 ${p}%`, right: p => `0 ${p}% 0 0`, up: p => `${p}% 0 0 0`, down: p => `0 0 ${p}% 0` };
    const WIPE_OUT = { left: p => `0 ${p}% 0 0`, right: p => `0 0 0 ${p}%`, up: p => `0 0 ${p}% 0`, down: p => `${p}% 0 0 0` };

    // One clip's share of a transition: the entering clip plays it forwards, the clip it replaces plays the
    // complement. Reduced motion turns every moving transition into a fade.
    function transitionStyle(el, enter, exit) {
      el.style.opacity = ''; el.style.transform = ''; el.style.clipPath = '';
      const opacity = [], transforms = [], clipPaths = [];
      for (const [t, entering] of [[enter, true], [exit, false]]) {
        if (!t) continue;
        const p = t.progress, shown = entering ? p : 1 - p;
        const kind = reduced && t.kind !== 'fade' ? 'fade' : t.kind;
        const [ax, ay] = AXIS[t.direction];
        if (kind === 'fade') opacity.push(shown);
        else if (kind === 'zoom') {
          opacity.push(shown);
          if (entering) transforms.push(`scale(${SLIDE_ZOOM_FROM + (1 - SLIDE_ZOOM_FROM) * p})`);
        } else if (kind === 'slide') {
          const d = entering ? (1 - p) * 100 : p * 100, s = entering ? -1 : 1;
          transforms.push(`translate(${s * ax * d}%, ${s * ay * d}%)`);
        } else if (kind === 'wipe') clipPaths.push(`inset(${(entering ? WIPE_IN : WIPE_OUT)[t.direction](entering ? (1 - p) * 100 : p * 100)})`);
      }
      if (opacity.length) el.style.opacity = String(opacity.reduce((a, b) => a * b, 1));
      if (transforms.length) el.style.transform = transforms.join(' ');
      if (clipPaths.length) el.style.clipPath = clipPaths[clipPaths.length - 1];
    }

    function walk(items, fn) {
      for (const item of items) { fn(item); if (item.children) walk(item.children, fn); }
    }

    let frameNow = 0;
    async function draw(n) {
      frameNow = n;
      const list = core.evaluateFrame({ ...input, frame: n }, () => core.evaluate(comp, n, props));
      const visible = new Set(list.clips.map(c => c.id));
      for (const [id, { el, hud }] of clips) { el.classList.toggle('z2-off', !visible.has(id)); hud.classList.toggle('z2-off', !visible.has(id)); }
      const cam = list.camera;
      camera.style.transform = `translate(${W / 2}px, ${H / 2}px) rotate(${cam.rotate}deg) scale(${cam.zoom}) translate(${-cam.x}px, ${-cam.y}px)`;
      if (blurNode) {
        // A box blur as long as the shutter-open motion looks like a Gaussian of about a third of that length. The
        // filter runs in the camera's own space, before its zoom, so screen px are divided by the zoom.
        const [mx, my] = core.screenMotion(list.cameraPrev, cam, W, H).map(d => Math.min(BLUR.max, d * shutter / 360 / BLUR.sigma));
        const on = mx > BLUR.min || my > BLUR.min;
        blurNode.setAttribute('stdDeviation', on ? `${(mx / cam.zoom).toFixed(2)} ${(my / cam.zoom).toFixed(2)}` : '0 0');
        camera.style.filter = on ? 'url(#z2-mblur)' : '';
      }
      waits.length = 0;
      list.clips.forEach((c, i) => {
        const { el, hud, badge, strip } = clips.get(c.id);
        el.style.zIndex = String(i + 1);
        hud.style.zIndex = String(HUD_Z + i);
        transitionStyle(el, c.transition, c.exit);
        transitionStyle(hud, c.transition, c.exit);
        for (const item of c.layers) drawLayer(item, c);
        // The topmost visible capture decides the label and caption.
        const shots = [];
        walk(c.layers, item => { if (item.type === 'capture' && num(item.values.opacity, 1) > 0) shots.push(item.values); });
        const top = shots[shots.length - 1];
        badge.textContent = shots.some(v => v.label === 'synthetic') ? 'Simulated' : '';
        strip.textContent = top && top.caption ? String(top.caption) : '';
        badge.style.display = badge.textContent ? '' : 'none';
        strip.style.display = strip.textContent ? '' : 'none';
      });
      drawVoices(n);
      await Promise.all(waits.splice(0));
      await twoFrames();
      return n;
    }

    // Requests are coalesced: while a frame is drawing, only the newest request is drawn next, and every
    // waiting caller resolves when it is on screen. Export awaits each frame, so it never skips one.
    let wanted = null, running = false, waiting = [];
    async function pump() {
      if (running) return;
      running = true;
      while (wanted !== null) {
        const n = wanted;
        wanted = null;
        try {
          await draw(n);
          if (wanted === null) waiting.splice(0).forEach(w => w.resolve(n));
        } catch (err) {
          fail(err);
          waiting.splice(0).forEach(w => w.reject(err));
        }
      }
      running = false;
    }
    function seekFrame(n) {
      if (!Number.isSafeInteger(n) || n < 0) return Promise.reject(new RangeError('frame must be a nonnegative integer'));
      wanted = Math.min(n, comp.duration - 1);
      return new Promise((resolve, reject) => { waiting.push({ resolve, reject }); pump(); });
    }
    const frameAt = ms => Math.min(comp.duration - 1, core.frameAtMilliseconds(Math.max(0, Number(ms) || 0), fps));
    const seek = ms => seekFrame(frameAt(ms));

    async function ready() {
      await Promise.all(fontLoads);
      await document.fonts.ready;
      await Promise.all(media.map(m => m.tagName === 'IMG' ? m.decode()
        : new Promise((resolve, reject) => {
          if (m.readyState >= 2) return resolve();
          m.addEventListener('loadeddata', resolve, { once: true });
          m.addEventListener('error', () => reject(new Error('a video asset could not be loaded')), { once: true });
        })));
      // Room content is measured with its final fonts, so the fit is the same on every machine that has them.
      rooms.forEach(fitRoom);
      await Promise.all(uiMounts);
    }

    return { comp, props, durationMs, seekFrame, seek, frameAt, ready, info: { version: 2, id: scene.id, composition: comp.id, fps, duration: comp.duration, duration_ms: durationMs, width: W, height: H } };
  }

  async function boot() {
    try {
      const opts = data('zm-options') || {};
      const scene = data('zm-scene');
      if (!scene) throw new Error('no scene was provided');
      if (opts.render) document.body.classList.add('render');
      const r = create(scene, opts);
      window.__scene = r.info;
      window.__seekFrame = r.seekFrame;
      window.__seek = r.seek;
      // The mixer's plan (systems 6e90b5b7, b1e68bc7): what sounds, when and how loud, before any frame is drawn.
      window.__audioPlan = () => core.audioPlan(r.comp, r.props);
      await r.ready();
      if (opts.render) await r.seekFrame(0);
      else {
        const frame = document.getElementById('frame');
        const fit = () => {
          const k = Math.min(innerWidth / r.comp.width, (innerHeight - 64) / r.comp.height);
          frame.style.transform = `scale(${k})`;
          frame.style.height = r.comp.height * k + 'px';
        };
        fit();
        addEventListener('resize', fit);
        if (!window.ZCMotion) throw new Error('the preview player needs motion.js');
        // The v1 player drives the same frame path through a millisecond adapter.
        window.ZCMotion.player({ duration: r.durationMs, seek: ms => r.seek(ms) }, document.body, { embed: opts.embed === true, t: Number(opts.t) || 0 });
        await r.seek(Number(opts.t) || 0);
      }
      window.__ready = true;
    } catch (err) {
      fail(err);
    }
  }
  boot();
})();
