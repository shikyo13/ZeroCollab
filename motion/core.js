// Generated from motion/core. Run node motion/build-core.mjs; edit the TypeScript source.
var ZCMotionCore = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // motion/core/index.ts
  var index_exports = {};
  __export(index_exports, {
    LABEL_H: () => LABEL_H,
    PERSON: () => PERSON,
    clamp: () => clamp,
    cubicBezier: () => cubicBezier,
    easing: () => easing,
    evaluateFrame: () => evaluateFrame,
    finite: () => finite,
    frameAtMilliseconds: () => frameAtMilliseconds,
    frameContext: () => frameContext,
    frameToMilliseconds: () => frameToMilliseconds,
    freeze: () => freeze,
    integer: () => integer,
    interpolate: () => interpolate,
    interpolateColors: () => interpolateColors,
    layoutStage: () => layoutStage,
    loop: () => loop,
    measureSpring: () => measureSpring,
    overlap: () => overlap,
    personRect: () => personRect,
    placeBubble: () => placeBubble,
    seats: () => seats,
    sequence: () => sequence,
    series: () => series,
    slotTargets: () => slotTargets,
    spring: () => spring,
    springStep: () => springStep,
    stagger: () => stagger,
    validateFps: () => validateFps
  });

  // motion/core/math.ts
  function finite(value, name) {
    if (!Number.isFinite(value)) throw new RangeError(`${name} must be finite`);
    return value;
  }
  function integer(value, name, minimum = -Number.MAX_SAFE_INTEGER) {
    if (!Number.isSafeInteger(value) || value < minimum) throw new RangeError(`${name} must be an integer >= ${minimum}`);
    return value;
  }
  var clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
  function cubicBezier(x1, y1, x2, y2) {
    [x1, y1, x2, y2].forEach((v, i) => finite(v, `bezier control ${i}`));
    if (x1 < 0 || x1 > 1 || x2 < 0 || x2 > 1) throw new RangeError("Bezier x controls must be in [0, 1]");
    const axis = (u, a, b) => 3 * (1 - u) ** 2 * u * a + 3 * (1 - u) * u * u * b + u ** 3;
    return (progress) => {
      finite(progress, "progress");
      if (progress <= 0) return 0;
      if (progress >= 1) return 1;
      let low = 0, high = 1;
      for (let i = 0; i < 48; i++) {
        const u = (low + high) / 2;
        if (axis(u, x1, x2) < progress) low = u;
        else high = u;
      }
      return axis((low + high) / 2, y1, y2);
    };
  }
  var easing = Object.freeze({
    linear: ((p) => p),
    ease: cubicBezier(0.25, 0.1, 0.25, 1),
    easeIn: cubicBezier(0.42, 0, 1, 1),
    easeOut: cubicBezier(0, 0, 0.58, 1),
    easeInOut: cubicBezier(0.42, 0, 0.58, 1),
    "ease-in": cubicBezier(0.42, 0, 1, 1),
    "ease-out": cubicBezier(0, 0, 0.58, 1),
    "ease-in-out": cubicBezier(0.42, 0, 0.58, 1),
    "step-start": ((p) => p < 0 ? 0 : 1),
    "step-end": ((p) => p < 1 ? 0 : 1),
    cubicBezier
  });
  function interpolate(value, input, output, options = {}) {
    finite(value, "value");
    if (input.length < 2 || input.length !== output.length) throw new RangeError("Interpolation needs equal ranges of at least two values");
    input.forEach((v, i) => {
      finite(v, `input[${i}]`);
      finite(output[i], `output[${i}]`);
      if (i && v <= input[i - 1]) throw new RangeError("Input range must be strictly increasing");
    });
    const left = options.extrapolateLeft ?? options.extrapolate ?? "clamp";
    const right = options.extrapolateRight ?? options.extrapolate ?? "clamp";
    if (![left, right].every((v) => v === "clamp" || v === "extend")) throw new RangeError("Unknown extrapolation");
    if (value < input[0] && left === "clamp") return output[0];
    const end = input.length - 1;
    if (value > input[end] && right === "clamp") return output[end];
    let segment = 0;
    while (segment < end - 1 && value >= input[segment + 1]) segment++;
    const progress = (value - input[segment]) / (input[segment + 1] - input[segment]);
    const shaped = progress < 0 || progress > 1 ? progress : (options.easing ?? easing.linear)(progress);
    return output[segment] + (output[segment + 1] - output[segment]) * finite(shaped, "easing result");
  }
  function color(value) {
    const hex = /^#([\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i.exec(value.trim());
    if (hex) {
      let s = hex[1];
      if (s.length < 5) s = [...s].map((c) => c + c).join("");
      return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16), s.length === 8 ? parseInt(s.slice(6, 8), 16) / 255 : 1];
    }
    const rgb = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i.exec(value.trim());
    if (!rgb) throw new RangeError("Colors must be hex, rgb() or rgba() values");
    const channels = [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] === void 0 ? 1 : Number(rgb[4])];
    if (channels.some((v, i) => !Number.isFinite(v) || v < 0 || v > (i === 3 ? 1 : 255))) throw new RangeError("Color channel out of range");
    return channels;
  }
  function interpolateColors(value, input, output, options = {}) {
    const colors = output.map(color);
    const channels = [0, 1, 2, 3].map((channel) => interpolate(value, input, colors.map((c) => c[channel]), options));
    return `rgba(${channels.slice(0, 3).map((v) => Math.round(clamp(v, 0, 255))).join(", ")}, ${Number(clamp(channels[3]).toFixed(6))})`;
  }

  // motion/core/frame.ts
  function validateFps(fps) {
    integer(fps.num, "fps.num", 1);
    integer(fps.den, "fps.den", 1);
    return fps;
  }
  function frameToMilliseconds(frame, fps) {
    integer(frame, "frame");
    validateFps(fps);
    return finite(frame * 1e3 * fps.den / fps.num, "frame time");
  }
  function frameAtMilliseconds(milliseconds, fps) {
    finite(milliseconds, "milliseconds");
    validateFps(fps);
    const value = Math.max(0, milliseconds) * fps.num / (1e3 * fps.den);
    const nearest = Math.round(value);
    const frame = Math.abs(value - nearest) <= Number.EPSILON * 4 * Math.max(1, value) ? nearest : Math.floor(value);
    return integer(frame, "frame", 0);
  }
  function frameContext(input) {
    if (!input.composition?.id || !/^[\da-f]{64}$/i.test(input.composition.revision)) throw new RangeError("Composition id and SHA256 revision are required");
    integer(input.frame, "frame", 0);
    integer(input.seed, "seed", 0);
    if (input.seed > 2147483647) throw new RangeError("seed must be in [0, 2^31-1]");
    const ids = /* @__PURE__ */ new Set();
    const assets = input.assets.map((asset) => {
      const ref = /^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/.test(asset.ref) || /^motion:(?=.{1,120}$)[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/.test(asset.ref);
      if (!asset.id || ids.has(asset.id) || !/^[\da-f]{64}$/i.test(asset.sha256) || !ref || !["image", "video", "audio", "font", "capture"].includes(asset.kind) || typeof asset.src !== "string" || !asset.src.startsWith("data:")) throw new RangeError("Assets need unique ids, SHA256 hashes, safe refs and server-inlined data sources");
      ids.add(asset.id);
      return Object.freeze({ ...asset });
    });
    const milliseconds = frameToMilliseconds(input.frame, input.fps);
    return Object.freeze({ composition: Object.freeze({ ...input.composition }), fps: Object.freeze({ ...input.fps }), frame: input.frame, seed: input.seed, assets: Object.freeze(assets), seconds: milliseconds / 1e3, milliseconds });
  }
  function evaluateFrame(input, evaluate) {
    return evaluate(frameContext(input));
  }

  // motion/core/spring.ts
  function criticalState(displacement, velocity, seconds, omega) {
    const e = Math.exp(-omega * seconds), step = (velocity + omega * displacement) * seconds;
    return [(displacement + step) * e, (velocity - omega * step) * e];
  }
  function springStep(x, velocity, target, dt, omega = 9) {
    [x, velocity, target, dt, omega].forEach((v, i) => finite(v, `spring step ${i}`));
    if (dt < 0 || omega <= 0) throw new RangeError("Spring dt must be nonnegative and omega positive");
    const [position, nextVelocity] = criticalState(x - target, velocity, dt, omega);
    return [target + position, nextVelocity];
  }
  function model(options) {
    const mass = options.mass ?? 1, stiffness = options.stiffness ?? 120, damping = options.damping ?? 20;
    [mass, stiffness, damping, options.from ?? 0, options.to ?? 1, options.velocity ?? 0].forEach((v, i) => finite(v, `spring input ${i}`));
    if (mass <= 0 || stiffness <= 0 || damping < 0) throw new RangeError("Spring mass/stiffness must be positive and damping nonnegative");
    const from = options.from ?? 0, to = options.to ?? 1, velocity = options.velocity ?? 0;
    const a = damping / (2 * mass), omega2 = stiffness / mass, discriminant = a * a - omega2, initial = from - to;
    const critical = Math.abs(discriminant) <= Number.EPSILON * 16 * Math.max(a * a, omega2);
    if (critical) {
      const b = velocity + a * initial;
      return {
        from,
        to,
        rest: initial === 0 && velocity === 0,
        a,
        monotonicAfter: a ? 1 / a : 0,
        value: (t) => criticalState(initial, velocity, t, a)[0],
        envelope: (t) => [Math.exp(-a * t) * (Math.abs(initial) + Math.abs(b) * t), Math.exp(-a * t) * (Math.abs(velocity) + a * Math.abs(b) * t)]
      };
    }
    if (discriminant < 0) {
      const w = Math.sqrt(-discriminant), b = (velocity + a * initial) / w;
      const positionBound = Math.hypot(initial, b), velocityBound = Math.hypot(-a * initial + w * b, -a * b - w * initial);
      return {
        from,
        to,
        rest: initial === 0 && velocity === 0,
        a,
        monotonicAfter: 0,
        value: (t) => Math.exp(-a * t) * (initial * Math.cos(w * t) + b * Math.sin(w * t)),
        envelope: (t) => [positionBound * Math.exp(-a * t), velocityBound * Math.exp(-a * t)]
      };
    }
    const q = Math.sqrt(discriminant), r1 = -omega2 / (a + q), r2 = -a - q;
    const first = (velocity - r2 * initial) / (r1 - r2), second = initial - first;
    return {
      from,
      to,
      rest: initial === 0 && velocity === 0,
      a,
      monotonicAfter: 0,
      value: (t) => first * Math.exp(r1 * t) + second * Math.exp(r2 * t),
      envelope: (t) => [Math.abs(first) * Math.exp(r1 * t) + Math.abs(second) * Math.exp(r2 * t), Math.abs(r1 * first) * Math.exp(r1 * t) + Math.abs(r2 * second) * Math.exp(r2 * t)]
    };
  }
  function spring(frame, fps, options = {}) {
    const m = model(options), seconds = Math.max(0, frameToMilliseconds(frame, fps)) / 1e3;
    const value = seconds === 0 ? m.from : m.to + m.value(seconds);
    return options.overshootClamping ? clamp(value, Math.min(m.from, m.to), Math.max(m.from, m.to)) : value;
  }
  function measureSpring(fps, options = {}) {
    validateFps(fps);
    const m = model(options);
    const threshold = options.threshold ?? 1e-3, velocityThreshold = options.velocityThreshold ?? 1e-3;
    if (!(finite(threshold, "threshold") > 0) || !(finite(velocityThreshold, "velocityThreshold") > 0)) throw new RangeError("Settle thresholds must be positive");
    if (m.rest) return 0;
    if (m.a === 0) return Infinity;
    const settled = (t) => {
      const [position, velocity] = m.envelope(t);
      return position <= threshold && velocity <= velocityThreshold;
    };
    let low = m.monotonicAfter, high = Math.max(1, low);
    const maximum = Number.MAX_SAFE_INTEGER * fps.den / fps.num;
    while (!settled(high)) {
      high *= 2;
      if (!Number.isFinite(high) || high > maximum) return Infinity;
    }
    if (!settled(low)) for (let i = 0; i < 64; i++) {
      const mid = (low + high) / 2;
      if (settled(mid)) high = mid;
      else low = mid;
    }
    else high = low;
    return Math.ceil(high * fps.num / fps.den);
  }

  // motion/core/timing.ts
  function sequence(frame, options) {
    integer(frame, "frame");
    integer(options.from, "from");
    integer(options.duration, "duration", 1);
    const local = frame - options.from;
    return { active: local >= 0 && local < options.duration, frame: local + integer(options.offset ?? 0, "offset"), progress: options.duration === 1 ? local < 0 ? 0 : 1 : clamp(local / (options.duration - 1)) };
  }
  function series(frame, clips, from = 0) {
    integer(from, "from");
    let start = from;
    return clips.map((clip) => {
      const sample = sequence(frame, { ...clip, from: start });
      start += clip.duration;
      integer(start, "series end");
      return sample;
    });
  }
  function loop(frame, options) {
    integer(frame, "frame");
    integer(options.duration, "duration", 1);
    const from = integer(options.from ?? 0, "from");
    const count = options.count === void 0 ? Infinity : integer(options.count, "count", 1);
    const period = options.pingPong && options.duration > 1 ? 2 * (options.duration - 1) : options.duration;
    const relative = frame - from, iteration = Math.floor(relative / period);
    const phase = (relative % period + period) % period;
    const backwards = !!options.pingPong && phase >= options.duration;
    const local = backwards ? period - phase : phase;
    return { active: relative >= 0 && iteration < count, frame: local, progress: options.duration === 1 ? 1 : local / (options.duration - 1), iteration, direction: backwards ? -1 : 1 };
  }
  function freeze(frame, options) {
    integer(frame, "frame");
    integer(options.at, "at");
    const from = integer(options.from ?? 0, "from");
    const duration = options.duration === void 0 ? Infinity : integer(options.duration, "duration", 1);
    return frame >= from && frame < from + duration ? options.at : frame;
  }
  function stagger(frame, index, delay) {
    integer(frame, "frame");
    integer(index, "index", 0);
    integer(delay, "delay", 0);
    return integer(frame - index * delay, "staggered frame");
  }

  // motion/core/layout.ts
  function seats(count, options) {
    integer(count, "seat count", 0);
    const columns = integer(options.columns ?? 3, "columns", 1), spacing = options.spacing ?? { x: 114, y: 138 };
    [options.origin.x, options.origin.y, spacing.x, spacing.y].forEach((v, i) => finite(v, `seat input ${i}`));
    if (spacing.x < 0 || spacing.y < 0) throw new RangeError("Seat spacing must be nonnegative");
    return Array.from({ length: count }, (_, i) => {
      const row = Math.floor(i / columns), inRow = Math.min(columns, count - row * columns);
      return { x: options.origin.x + (i % columns - (options.centered === false ? 0 : (inRow - 1) / 2)) * spacing.x, y: options.origin.y + row * spacing.y };
    });
  }
  function slotTargets(members, centers, options) {
    const groups = /* @__PURE__ */ new Map(), result = /* @__PURE__ */ Object.create(null);
    const ordered = [...members].sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    const ids = /* @__PURE__ */ new Set();
    ordered.forEach((member) => {
      integer(member.order, "roster order", 0);
      if (!member.id || ids.has(member.id)) throw new RangeError("Formation ids must be unique");
      ids.add(member.id);
      if (member.quiet || !member.group) {
        result[member.id] = seats(member.order + 1, { ...options.home, centered: false })[member.order];
      } else {
        if (!Object.hasOwn(centers, member.group)) throw new RangeError(`Missing formation center: ${member.group}`);
        const group = groups.get(member.group) ?? [];
        group.push(member);
        groups.set(member.group, group);
      }
    });
    for (const [key, group] of groups) {
      const points = seats(group.length, { ...options.group, origin: centers[key] });
      group.forEach((member, i) => {
        result[member.id] = points[i];
      });
    }
    return result;
  }

  // motion/core/stage.ts
  var PERSON = { w: 96, h: 74, thumbH: 46, quietScale: 0.72 };
  var SEAT_W = 120;
  var SEAT_H = 104;
  var GAP = 26;
  var LABEL_H = 34;
  var overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  function personRect(p, quiet, hasThumb) {
    const s = quiet ? PERSON.quietScale : 1;
    const w = PERSON.w * s, h = PERSON.h * s + (hasThumb && !quiet ? PERSON.thumbH : 0);
    return { x: p.x - w / 2, y: p.y - 26 * s, w, h };
  }
  function homeSeats(people, area) {
    const n = Math.max(1, people.length);
    const aspect = area.w / Math.max(1, area.h);
    const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt(n * aspect))));
    const rows = Math.ceil(n / cols);
    const cw = area.w / cols, ch = area.h / rows;
    const seats2 = /* @__PURE__ */ new Map();
    [...people].sort((a, b) => a.order - b.order).forEach((p, i) => {
      const r = Math.floor(i / cols), c = i % cols;
      const shift = r % 2 ? cw / 4 : -cw / 4;
      seats2.set(p.id, { x: area.x + cw * (c + 0.5) + (cols > 1 ? shift : 0), y: area.y + ch * (r + 0.5) });
    });
    return seats2;
  }
  function layoutStage(people, stage, reserved = [], previous) {
    const area = { x: stage.x + 60, y: stage.y + 70, w: Math.max(120, stage.w - 120), h: Math.max(120, stage.h - 130) };
    const home = homeSeats(people, area);
    const seats2 = new Map(home);
    const byGroup = /* @__PURE__ */ new Map();
    for (const p of people) if (p.group && !p.quiet) (byGroup.get(p.group) ?? byGroup.set(p.group, []).get(p.group)).push(p);
    const groups = [...byGroup].filter(([, m]) => m.length > 1).map(([group, members2]) => {
      members2.sort((a, b) => (a.joinedAt ?? 0) - (b.joinedAt ?? 0) || a.order - b.order);
      return { group, members: members2, since: members2[0].joinedAt ?? 0, label: members2.find((m) => m.label)?.label };
    }).sort((a, b) => a.since - b.since || (a.group < b.group ? -1 : 1));
    const members = new Set(groups.flatMap((g) => g.members.map((m) => m.id)));
    const taken = [...reserved, ...people.filter((p) => !members.has(p.id)).map((p) => personRect(home.get(p.id), p.quiet, p.hasThumb))];
    const clear = (r) => r.x >= area.x - 40 && r.y >= area.y - 50 && r.x + r.w <= area.x + area.w + 40 && r.y + r.h <= area.y + area.h + 40 && taken.every((t) => overlap({ x: r.x - GAP, y: r.y - GAP, w: r.w + 2 * GAP, h: r.h + 2 * GAP }, t) === 0);
    const formations = [];
    for (const g of groups) {
      const homes = g.members.map((m) => home.get(m.id));
      const cx = homes.reduce((s, h) => s + h.x, 0) / homes.length, cy = homes.reduce((s, h) => s + h.y, 0) / homes.length;
      const cols = g.members.length <= 3 ? g.members.length : Math.ceil(g.members.length / 2);
      const rows = Math.ceil(g.members.length / cols);
      const chipW = Math.min(320, 7 * (g.label?.length ?? 24) + 64);
      let placed = null;
      const before = previous?.get(g.group);
      if (before) {
        const rowH2 = SEAT_H + (g.members.some((m) => m.hasThumb) ? PERSON.thumbH : 0);
        const w = Math.max(cols * SEAT_W, chipW), h = rows * rowH2 + LABEL_H;
        const r2 = { x: before.x + before.w / 2 - w / 2, y: before.y, w, h };
        if (clear(r2)) placed = { r: r2, seatW: SEAT_W, rowH: rowH2 };
      }
      for (const scale of placed ? [] : [1, 0.86, 0.74]) {
        const seatW2 = SEAT_W * scale, rowH2 = (SEAT_H + (g.members.some((m) => m.hasThumb) ? PERSON.thumbH : 0)) * scale;
        const w = Math.max(cols * seatW2, chipW), h = rows * rowH2 + LABEL_H;
        for (let radius = 0; radius <= 520 && !placed; radius += 20) {
          const steps = radius === 0 ? 1 : Math.ceil(2 * Math.PI * radius / 20);
          for (let k = 0; k < steps; k++) {
            const ang = k / steps * 2 * Math.PI;
            const r2 = { x: cx + radius * Math.cos(ang) - w / 2, y: cy + radius * Math.sin(ang) - h / 2, w, h };
            if (clear(r2)) {
              placed = { r: r2, seatW: seatW2, rowH: rowH2 };
              break;
            }
          }
        }
        if (placed) break;
      }
      if (!placed) {
        const w = Math.max(cols * SEAT_W, chipW), rowH2 = SEAT_H;
        placed = { r: { x: cx - w / 2, y: cy - (rows * rowH2 + LABEL_H) / 2, w, h: rows * rowH2 + LABEL_H }, seatW: SEAT_W, rowH: rowH2 };
      }
      const { r, seatW, rowH } = placed;
      const mid = r.x + r.w / 2;
      g.members.forEach((m, i) => {
        const row = Math.floor(i / cols), inRow = Math.min(cols, g.members.length - row * cols), c = i % cols;
        seats2.set(m.id, { x: mid - inRow * seatW / 2 + seatW * (c + 0.5), y: r.y + LABEL_H + rowH * row + 34 });
      });
      taken.push(r);
      formations.push({ group: g.group, members: g.members.map((m) => m.id), box: r });
    }
    return { seats: seats2, formations, reserved };
  }
  function placeBubble(p, size, blockers, bounds, prefer) {
    const o = PERSON.w / 2 + 8;
    const c = {
      right: { x: p.x + o, y: p.y - size.h / 2 },
      left: { x: p.x - o - size.w, y: p.y - size.h / 2 },
      above: { x: p.x - size.w / 2, y: p.y - 34 - size.h },
      below: { x: p.x - size.w / 2, y: p.y + 56 },
      upRight: { x: p.x + o, y: p.y - 30 - size.h },
      upLeft: { x: p.x - o - size.w, y: p.y - 30 - size.h },
      downRight: { x: p.x + o, y: p.y + 40 },
      downLeft: { x: p.x - o - size.w, y: p.y + 40 }
    };
    const order = ["right", "left", "upRight", "upLeft", "above", "below", "downRight", "downLeft"];
    if (prefer) order.unshift(prefer);
    for (const side of order) {
      const rect = { ...c[side], ...size };
      if (rect.x < bounds.x || rect.y < bounds.y || rect.x + rect.w > bounds.x + bounds.w || rect.y + rect.h > bounds.y + bounds.h) continue;
      if (blockers.every((b) => overlap(rect, b) === 0)) return { side, rect };
    }
    return null;
  }
  return __toCommonJS(index_exports);
})();
