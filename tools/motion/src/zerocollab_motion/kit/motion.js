/*
  ZeroCollab motion kit, timeline adapter (option B in ../README.md).

  A scene is a list of beats (shared vocabulary v1, the same shape frontend's Live adapter
  projects from useLiveTeam):
    { at_ms, duration_ms, kind: agent|tool|board|handoff|browser|caption,
      actor, label, detail, state, target? }
  Real work adds stable run, task and artifact IDs; synthetic scenes say so in detail.

  This file turns beats into plain DOM inside a fixed 1920x1080 stage. Every motion is a Web
  Animations API animation on the document timeline, so the scene can be paused and seeked to
  any millisecond. Preview and export use the same seek, which is what makes a frame
  reproducible. In reduced motion every beat keeps the same labels and states at the same
  times, with no movement.

  Renderer contract, read by render.py and usable by any scene page that implements it:
    window.__scene  { version, duration, reduced, beats }
    window.__seek(ms) -> Promise   pauses everything at ms, resolves after style and paint
    window.__ready  true once fonts are loaded and the scene is built

  No dependencies. Plain script (not a module) so the page also works from file://.
*/
(function () {
  const VERSION = 1;
  const KINDS = ['agent', 'tool', 'board', 'handoff', 'browser', 'caption'];
  const EASE = 'cubic-bezier(.2,.7,.2,1)';

  // transient (v2 room layers only): handoff arrows and chips fade once the receiver has the work.
  // v1 pages never set it, so their frames are unchanged.
  const TRANSIENT_HOLD_MS = 1200, TRANSIENT_FADE_MS = 300;

  function create(stage, { duration, reduced, transient = false }) {
    const all = [];
    const tweened = new Set();   // node|prop driven by anim()
    const stepped = new Map();   // node|prop -> { node, prop, points } driven by steps()
    let ids = 0;
    const key = (node, prop) => (node.__zm = node.__zm || ++ids) + '|' + prop;

    // Several tweens may drive the same property of one element over time. Only the first
    // fills backwards, so a later tween never holds its start value over an earlier one. The
    // check is per property, so a tween sharing any property with an earlier one waits too.
    function anim(node, frames, { at, dur, easing = EASE }) {
      const keys = Object.keys(frames[0]).filter(k => k !== 'offset' && k !== 'easing').map(p => key(node, p));
      for (const k of keys) if (stepped.has(k)) throw new Error(`property ${k} is both stepped and tweened`);
      const fill = keys.some(k => tweened.has(k)) ? 'forwards' : 'both';
      keys.forEach(k => tweened.add(k));
      const a = node.animate(frames, { delay: at, duration: Math.max(dur, 1), fill, easing });
      a.pause();
      all.push(a);
    }

    // Discrete values, [[ms, value], ...]. Used for text states and every reduced-motion change,
    // so each switches exactly on its beat in both variants. All steps for one element property
    // are merged into a single animation when the scene is built, so a later call extends the
    // sequence instead of overriding an earlier one.
    function steps(node, prop, points) {
      const k = key(node, prop);
      if (tweened.has(k)) throw new Error(`property ${k} is both tweened and stepped`);
      const entry = stepped.get(k) || { node, prop, points: [] };
      entry.points.push(...points);
      stepped.set(k, entry);
    }

    function buildSteps() {
      for (const { node, prop, points } of stepped.values()) {
        // Stable sort by time; at equal times the later call wins.
        const sorted = points.map((p, i) => [p, i]).sort((a, b) => a[0][0] - b[0][0] || a[1] - b[1]).map(x => x[0]);
        const merged = [];
        for (const [t, v] of sorted) {
          const at = Math.max(t, 0);
          if (merged.length && merged[merged.length - 1][0] === at) merged[merged.length - 1][1] = v;
          else merged.push([at, v]);
        }
        const frames = merged.map(([t, v]) => ({ [prop]: v, offset: Math.min(t / duration, 1), easing: 'step-end' }));
        frames.push({ [prop]: merged[merged.length - 1][1], offset: 1 });
        const a = node.animate(frames, { duration, fill: 'both' });
        a.pause();
        all.push(a);
      }
      stepped.clear();
    }

    // Position inside the stage, ignoring the preview's scale transform.
    function pos(node) {
      let x = 0, y = 0, n = node;
      while (n && n !== stage) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; }
      return { x, y, w: node.offsetWidth, h: node.offsetHeight };
    }

    function el(tag, cls, text, parent = stage) {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      parent.append(n);
      return n;
    }

    // A stack of text spans switched by opacity, because text itself is not animatable.
    function textStates(parent, points, cls) {
      const box = el('span', 'zm-states ' + (cls || ''), null, parent);
      for (const text of [...new Set(points.map(p => p[1]))]) {
        const on = points.map(([t, v]) => [t, v === text ? '1' : '0']);
        if (on[0][0] > 0) on.unshift([0, '0']);
        steps(el('span', 'zm-state', text, box), 'opacity', on);
      }
    }

    const at0 = (points, initial) => (points.length && points[0][0] <= 0 ? points : [[0, initial], ...points]);

    function agentBeats(card, beats) {
      const idle = card.dataset.initial || 'Available';
      const points = at0(beats.map(b => [b.at_ms, b.state, b.duration_ms]), idle);
      textStates(card.stateHost, points, 'zm-agent-state');
      steps(card.dot, 'backgroundColor', points.map(([t, s]) => [t, s === idle ? 'var(--zm-muted)' : 'var(--zm-accent)']));
      // The card enters once, when the agent first leaves its idle state.
      const wake = points.find(([, s]) => s !== idle);
      if (wake) {
        const [t, , dur] = wake;
        if (reduced) steps(card, 'borderColor', [[0, 'var(--zm-line)'], [t, 'var(--zm-accent)']]);
        else anim(card, [{ transform: 'translateY(12px)', opacity: .55, borderColor: 'var(--zm-line)' },
                         { transform: 'translateY(0)', opacity: 1, borderColor: 'var(--zm-accent)' }], { at: t, dur: dur || 220 });
      }
    }

    // A running beat starts the call; a later beat with the same actor and label and any other
    // state (Completed, Failed) ends it. Without one the outcome is unknown, so the call stays
    // Running to the end of the scene: nothing invents a completion.
    function toolBeats(card, beats) {
      const isRun = b => String(b.state).toLowerCase() === 'running';
      const runs = beats.filter(isRun);
      for (const run of runs) {
        const finish = beats.find(b => !isRun(b) && b.label === run.label && b.at_ms >= run.at_ms);
        const start = run.at_ms;
        const row = el('div', 'zm-tool-row', null, card.toolHost);
        el('span', 'zm-tool-label', run.label, row);
        if (!finish) {
          textStates(row, [[start, run.state]], 'zm-tool-status');
          el('i', null, null, el('div', 'zm-bar', null, row));
        } else {
          textStates(row, [[start, run.state], [finish.at_ms, finish.state]], 'zm-tool-status');
        }
        const fill = finish ? el('i', null, null, el('div', 'zm-bar', null, row)) : null;
        steps(row, 'opacity', [[0, '0'], [start, '1']]);
        if (!reduced) anim(row, [{ transform: 'translateY(10px)' }, { transform: 'translateY(0)' }], { at: start, dur: 220 });
        if (!finish) continue;
        const end = finish.at_ms, outcome = finish.state;
        if (reduced) steps(fill, 'transform', [[0, 'scaleX(0)'], [end, 'scaleX(1)']]);
        else anim(fill, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { at: start, dur: end - start, easing: 'linear' });
        steps(fill, 'backgroundColor', [[0, 'var(--zm-accent)'], [end, String(outcome).toLowerCase() === 'failed' ? 'var(--zm-danger)' : 'var(--zm-ok)']]);
      }
    }

    // All moves of one card, in time order. Offsets are absolute from the card's home column,
    // and each move starts where the previous one ended.
    function boardMoves(card, home, moves) {
      const h = pos(home);
      let x = 0, y = 0;
      const points = [[0, 'translate(0px, 0px)']];
      for (const { to, beat } of moves) {
        const z = pos(to), nx = z.x - h.x, ny = z.y - h.y;
        const end = `translate(${nx}px, ${ny}px)`;
        if (reduced) points.push([beat.at_ms, end]);
        else anim(card, [{ transform: `translate(${x}px, ${y}px)` },
                         { transform: `translate(${(x + nx) / 2}px, ${(y + ny) / 2 - 36}px) rotate(1.5deg)`, offset: .5 },
                         { transform: end }], { at: beat.at_ms, dur: beat.duration_ms || 1200 });
        x = nx; y = ny;
      }
      if (reduced) steps(card, 'transform', points);
    }

    function handoffBeat(from, to, b) {
      const a = pos(from), z = pos(to);
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'zm-arrow');
      svg.setAttribute('viewBox', '0 0 1920 1080');
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = z.x, y2 = z.y + z.h / 2;
      const path = document.createElementNS(svg.namespaceURI, 'path');
      path.setAttribute('d', `M ${x1 + 8} ${y1} C ${x1 + 60} ${y1 - 50}, ${x2 - 60} ${y2 - 50}, ${x2 - 10} ${y2}`);
      const head = document.createElementNS(svg.namespaceURI, 'path');
      head.setAttribute('d', `M ${x2 - 28} ${y2 - 13} L ${x2 - 9} ${y2} L ${x2 - 28} ${y2 + 13}`);
      svg.append(path, head);
      const host = transient ? el('div', 'zm-transient') : stage;
      host.append(svg);
      const tag = el('div', 'zm-arrow-label', b.label, host);
      Object.assign(tag.style, { left: ((x1 + x2) / 2) + 'px', top: (Math.min(a.y, z.y) - 60) + 'px' });
      const len = path.getTotalLength(), dur = b.duration_ms || 900;
      path.style.strokeDasharray = String(len);
      if (reduced) steps(path, 'strokeDashoffset', [[0, String(len)], [b.at_ms, '0']]);
      else anim(path, [{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { at: b.at_ms, dur });
      steps(head, 'opacity', [[0, '0'], [reduced ? b.at_ms : b.at_ms + dur - 100, '1']]);
      steps(tag, 'opacity', [[0, '0'], [b.at_ms, '1']]);
      if (!transient) return;
      const gone = b.at_ms + dur + TRANSIENT_HOLD_MS;
      if (reduced) steps(host, 'opacity', [[0, '1'], [gone, '0']]);
      else anim(host, [{ opacity: 1 }, { opacity: 0 }], { at: gone, dur: TRANSIENT_FADE_MS, easing: 'ease-in' });
    }

    // Visual emphasis only: pointer-events none and aria-hidden. It never takes focus,
    // navigates or changes the shared browser. An unknown target stays generic: the ring
    // frames the whole browser panel instead of guessing an element.
    function browserBeat(target, b) {
      const p = pos(target);
      const ring = el('div', 'zm-emphasis');
      ring.setAttribute('aria-hidden', 'true');
      Object.assign(ring.style, { left: (p.x - 8) + 'px', top: (p.y - 8) + 'px', width: (p.w + 16) + 'px', height: (p.h + 16) + 'px' });
      const tag = el('div', 'zm-emphasis-label', b.label);
      // Beside the target when it fits, otherwise above it, right-aligned, so it never clips.
      if (p.x + p.w + 24 + 440 <= 1920) Object.assign(tag.style, { left: (p.x + p.w + 24) + 'px', top: (p.y + p.h / 2 - 22) + 'px' });
      else Object.assign(tag.style, { right: (1920 - p.x - p.w) + 'px', top: (p.y - 64) + 'px' });
      const until = b.at_ms + b.duration_ms;
      steps(ring, 'opacity', [[0, '0'], [b.at_ms, '1'], [until, '0']]);
      steps(tag, 'opacity', [[0, '0'], [b.at_ms, '1'], [until, '0']]);
      if (!reduced) anim(ring, [{ transform: 'scale(1.12)' }, { transform: 'scale(1)' }], { at: b.at_ms, dur: 220 });
    }

    function captionBeat(b) {
      const c = el('div', 'zm-caption', b.label);
      steps(c, 'opacity', [[0, '0'], [b.at_ms, '1'], [b.at_ms + b.duration_ms, '0']]);
      if (!reduced) anim(c, [{ transform: 'translateY(16px)' }, { transform: 'translateY(0)' }], { at: b.at_ms, dur: 220 });
    }

    // refs maps beat names to scene elements: agents by actor, board columns and cards by
    // name, browser targets by name. A beat naming something missing is an error, not a guess.
    function play(beats, refs) {
      const need = (map, key, beat) => {
        if (!map[key]) throw new Error(`beat at ${beat.at_ms} ms names unknown ${beat.kind} target "${key}"`);
        return map[key];
      };
      for (const b of beats) {
        if (!KINDS.includes(b.kind)) throw new Error(`unknown beat kind "${b.kind}"`);
        if (!(b.at_ms >= 0 && b.at_ms <= duration)) throw new Error(`beat "${b.label}" starts outside the scene`);
      }
      const by = kind => beats.filter(b => b.kind === kind);
      for (const b of by('agent')) need(refs.agents, b.actor, b);
      // Every agent shows a state, including one with no beats, which stays idle throughout.
      for (const [actor, card] of Object.entries(refs.agents)) agentBeats(card, by('agent').filter(b => b.actor === actor));
      for (const actor of new Set(by('tool').map(b => b.actor))) {
        toolBeats(need(refs.agents, actor, { kind: 'tool', at_ms: 0 }), by('tool').filter(b => b.actor === actor));
      }
      const boardBeats = by('board').slice().sort((a, b) => a.at_ms - b.at_ms);
      for (const label of new Set(boardBeats.map(b => b.label))) {
        const mine = boardBeats.filter(b => b.label === label);
        const card = need(refs.cards, label, mine[0]);
        const home = need(refs.columns, card.dataset.column, mine[0]);
        boardMoves(card, home, mine.map(b => ({ to: need(refs.columns, b.state, b), beat: b })));
      }
      for (const b of by('handoff')) handoffBeat(need(refs.agents, b.actor, b), need(refs.agents, b.target, b), b);
      for (const b of by('browser')) browserBeat(refs.browser[b.target] || refs.browser.panel, b);
      for (const b of by('caption')) captionBeat(b);
      buildSteps();
    }

    function seek(t) {
      const ms = Math.max(0, Math.min(duration, t));
      for (const a of all) { a.pause(); a.currentTime = ms; }
      return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(ms))));
    }

    return { el, pos, play, seek, duration, reduced };
  }

  // Preview player: the same seek path as export, driven by a wall clock. It never autoplays and
  // owns no motion switch: the Motion page's shared Full/Reduced choice reloads the frame.
  // opts: { embed: report time to the embedding page, t: start paused at this many ms }.
  function player(scene, host, opts = {}) {
    const bar = document.createElement('div');
    bar.className = 'zm-player';
    bar.innerHTML = `
      <button type="button">Play</button>
      <input type="range" min="0" max="${scene.duration}" step="${1000 / 30}" value="0" aria-label="Scene time">
      <output>0.00 s</output>`;
    host.append(bar);
    const btn = bar.querySelector('button'), range = bar.querySelector('input'), out = bar.querySelector('output');
    let t = 0, playing = false, origin = 0, reported = 0;
    // Preview-only bridge for the embedding page (frontend daeb1e97): this frame reports its time
    // and whether it is playing, so a Full/Reduced switch can reopen it paused at the same time
    // (opts.t). One way, these two fields only; it accepts no messages and exposes nothing else.
    const report = force => {
      if (!opts.embed || window.parent === window) return;
      const now = performance.now();
      if (!force && now - reported < 100) return;
      reported = now;
      window.parent.postMessage({ source: 'zerocollab-motion', version: 1, time_ms: Math.round(t), playing }, '*');
    };
    const show = ms => {
      t = ms; range.value = ms; out.textContent = (ms / 1000).toFixed(2) + ' s';
      report(false);
      return scene.seek(ms);
    };
    const stop = label => { playing = false; btn.textContent = label; report(true); };
    const tick = now => {
      if (!playing) return;
      const ms = now - origin;
      if (ms >= scene.duration) { show(scene.duration); stop('Replay'); return; }
      show(ms);
      requestAnimationFrame(tick);
    };
    btn.onclick = () => {
      if (playing) return stop('Play');
      if (t >= scene.duration) t = 0;
      playing = true; btn.textContent = 'Pause';
      origin = performance.now() - t;
      report(true);
      requestAnimationFrame(tick);
    };
    range.oninput = () => { stop('Play'); show(Number(range.value)); report(true); };
    const start = Number(opts.t);
    show(Number.isFinite(start) ? Math.max(0, Math.min(scene.duration, start)) : 0);
    report(true);
  }

  // Declarative layout: saved scenes are data only, never code. Elements are created in a fixed
  // order (title, agents, board, browser, cards) and return the refs the beats name.
  //   { title, agents: [{ id, name, x, y }], board: { x, y, columns: [..] },
  //     browser: { title, nav: [..], lines } (optional), cards: [{ id, title, meta, column }] }
  function layout(m, spec) {
    const refs = { agents: {}, columns: {}, cards: {}, browser: {} };
    if (spec.title) m.el('div', 'zm-title', spec.title);
    for (const a of spec.agents || []) {
      const card = m.el('div', 'zm-agent');
      card.id = a.id;
      Object.assign(card.style, { left: a.x + 'px', top: a.y + 'px' });
      m.el('div', 'zm-agent-name', a.name, card);
      const row = m.el('div', 'zm-agent-row', null, card);
      card.dot = m.el('span', 'zm-dot', null, row);
      card.stateHost = row;
      card.toolHost = m.el('div', 'zm-tool', null, card);
      refs.agents[a.id] = card;
    }
    if (spec.board) {
      const board = m.el('div', 'zm-board');
      Object.assign(board.style, { left: spec.board.x + 'px', top: spec.board.y + 'px' });
      for (const name of spec.board.columns) {
        const col = m.el('div', 'zm-col', null, board);
        m.el('div', 'zm-col-title', name, col);
        refs.columns[name] = col;
      }
    }
    if (spec.browser) {
      const browser = m.el('div', 'zm-browser');
      m.el('div', 'zm-browser-bar', spec.browser.title, browser);
      const app = m.el('div', 'zm-browser-app', null, browser);
      const nav = m.el('div', 'zm-browser-nav', null, app);
      for (const item of spec.browser.nav || []) m.el('div', 'zm-browser-item', item, nav);
      const body = m.el('div', 'zm-browser-body', null, app);
      for (let i = 0; i < (spec.browser.lines || 0); i++) m.el('div', 'zm-browser-line', null, body);
      refs.browser.panel = browser;
      refs.browser.sidebar = nav;
    }
    for (const c of spec.cards || []) {
      const card = m.el('div', 'zm-card');
      card.id = c.id;
      card.dataset.column = c.column;
      m.el('div', 'zm-card-title', c.title, card);
      if (c.meta) m.el('div', 'zm-card-meta', c.meta, card);
      const column = refs.columns[c.column];
      if (!column) throw new Error(`card "${c.id}" starts in unknown column "${c.column}"`);
      const home = m.pos(column);
      Object.assign(card.style, { left: (home.x + 20) + 'px', top: (home.y + 70) + 'px', width: (home.w - 40) + 'px' });
      refs.cards[c.id] = card;
    }
    return refs;
  }

  window.ZCMotion = { VERSION, KINDS, create, layout, player };
})();
