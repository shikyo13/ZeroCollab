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
    const props = opts.props && typeof opts.props === 'object' ? opts.props : {};
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

    const fontLoads = [];
    for (const a of assets.values()) {
      if (a.kind !== 'font') continue;
      // Bytes, not a URL: the face never fetches, so the page needs no font-src.
      const face = new FontFace('zm-' + a.id, decodeDataUrl(a.src));
      document.fonts.add(face);
      fontLoads.push(face.load());
    }

    const nodes = new Map();    // layer id -> { el, type, ... }
    const clips = new Map();    // clip id -> { el, clip }
    const rooms = [];           // room layers, to fit once fonts have loaded
    const media = [];           // img and video elements to wait for

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
        case 'group': for (const child of layer.layers || []) if (!child.hidden) buildLayer(child, el, clip); break;
        case 'room': buildRoom(node, clip); break;
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
        stage.append(el);
        for (const layer of clip.layers) if (!layer.hidden) buildLayer(layer, el, clip);
        // A capture's honesty label and caption belong to the frame, not the picture: zooming into a
        // capture must never scale them out of view. They sit above the clip's layers.
        const label = (cls, place) => { const n = document.createElement('div'); n.className = cls; Object.assign(n.style, place); el.append(n); return n; };
        clips.set(clip.id, { el, clip,
          badge: label('z2-badge', { left: px(Math.round(W * LABEL.inset)), top: px(Math.round(H * LABEL.inset)) }),
          strip: label('z2-strip', { bottom: px(Math.round(H * LABEL.inset)), maxWidth: px(Math.round(W * LABEL.stripWidth)) }) });
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
      if (v.font_asset) return `"zm-${asset(v.font_asset, ['font']).id}", var(--zm-font)`;
      return v.font ? `"${String(v.font).replace(/["\\]/g, '')}", var(--zm-font)` : '';
    }

    function drawText(node, v) {
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
      });
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

    const waits = [];
    function seekVideo(node, v, contentFrame) {
      const seconds = (num(v.trim, 0) + contentFrame * num(v.rate, 1)) * fps.den / fps.num;
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
        case 'text': transform(node, v); drawText(node, v); break;
        case 'image': case 'capture': case 'video':
          transform(node, v, { width: W, height: H });
          (node.img || node.video).style.objectFit = str(v.fit, 'contain');
          if (node.video) seekVideo(node, v, clipDraw.contentFrame);
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
          for (const child of item.children || []) drawLayer(child, clipDraw);
          break;
        case 'room':
          node.room.seek(core.frameToMilliseconds(clipDraw.contentFrame, fps));
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

    async function draw(n) {
      const list = core.evaluateFrame({ ...input, frame: n }, () => core.evaluate(comp, n, props));
      const visible = new Set(list.clips.map(c => c.id));
      for (const [id, { el }] of clips) el.classList.toggle('z2-off', !visible.has(id));
      waits.length = 0;
      list.clips.forEach((c, i) => {
        const { el, badge, strip } = clips.get(c.id);
        el.style.zIndex = String(i + 1);
        transitionStyle(el, c.transition, c.exit);
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
    }

    return { comp, durationMs, seekFrame, seek, frameAt, ready, info: { version: 2, id: scene.id, composition: comp.id, fps, duration: comp.duration, duration_ms: durationMs, width: W, height: H } };
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
