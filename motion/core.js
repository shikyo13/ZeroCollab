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

  // core/index.ts
  var index_exports = {};
  __export(index_exports, {
    AUDIO_PLAN_VERSION: () => AUDIO_PLAN_VERSION,
    IDENTITY: () => IDENTITY,
    LABEL_H: () => LABEL_H,
    NO_PROPS: () => NO_PROPS,
    PERSON: () => PERSON,
    SPRING_PRESETS: () => SPRING_PRESETS,
    audioPlan: () => audioPlan,
    blendViews: () => blendViews,
    cameraAt: () => cameraAt,
    cameraKeyView: () => cameraKeyView,
    cameraKeys: () => cameraKeys,
    clamp: () => clamp,
    compose: () => compose,
    compositionProps: () => compositionProps,
    cubicBezier: () => cubicBezier,
    cursorTrack: () => cursorTrack,
    easing: () => easing,
    evaluate: () => evaluate,
    evaluateFrame: () => evaluateFrame,
    finite: () => finite,
    followKeys: () => followKeys,
    followShots: () => followShots,
    frameAtMilliseconds: () => frameAtMilliseconds,
    frameContext: () => frameContext,
    frameToMilliseconds: () => frameToMilliseconds,
    freeze: () => freeze,
    integer: () => integer,
    interpolate: () => interpolate,
    interpolateColors: () => interpolateColors,
    layerAffine: () => layerAffine,
    layerToComposition: () => layerToComposition,
    layoutStage: () => layoutStage,
    loop: () => loop,
    mapRegion: () => mapRegion,
    measureSpring: () => measureSpring,
    noise: () => noise,
    overlap: () => overlap,
    pairCache: () => pairCache,
    personRect: () => personRect,
    placeBubble: () => placeBubble,
    random: () => random,
    regionView: () => regionView,
    sampleField: () => sampleField,
    screenMotion: () => screenMotion,
    seats: () => seats,
    segmentEasing: () => segmentEasing,
    sequence: () => sequence,
    series: () => series,
    slotTargets: () => slotTargets,
    spring: () => spring,
    springStep: () => springStep,
    stagger: () => stagger,
    timeMap: () => timeMap,
    toScreen: () => toScreen,
    toWorld: () => toWorld,
    validateFps: () => validateFps
  });

  // core/math.ts
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
    // Demo presets (28cb5faf): camera and title moves that start decisively and land softly.
    "expo-out": cubicBezier(0.16, 1, 0.3, 1),
    "expo-in-out": cubicBezier(0.87, 0, 0.13, 1),
    premium: cubicBezier(0.65, 0, 0.35, 1),
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

  // core/frame.ts
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
  var ASSET_KINDS = ["image", "video", "audio", "font", "capture"];
  var UPLOAD_REF = /^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/;
  var KIT_REF = /^motion:(?=.{1,120}$)[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/;
  var FILE_REF = /^fileref:[0-9a-f]{16}$/;
  var FILEREF_KINDS = ["image", "video", "audio", "capture"];
  var safeRef = (asset) => UPLOAD_REF.test(asset.ref) || KIT_REF.test(asset.ref) || FILE_REF.test(asset.ref) && FILEREF_KINDS.includes(asset.kind);
  function frameContext(input) {
    if (!input.composition?.id || !/^[\da-f]{64}$/i.test(input.composition.revision)) throw new RangeError("Composition id and SHA256 revision are required");
    integer(input.frame, "frame", 0);
    integer(input.seed, "seed", 0);
    if (input.seed > 2147483647) throw new RangeError("seed must be in [0, 2^31-1]");
    const ids = /* @__PURE__ */ new Set();
    const assets = input.assets.map((asset) => {
      if (!asset.id || ids.has(asset.id) || !/^[\da-f]{64}$/i.test(asset.sha256) || !safeRef(asset) || !ASSET_KINDS.includes(asset.kind) || typeof asset.src !== "string" || !asset.src.startsWith("data:")) throw new RangeError("Assets need unique ids, SHA256 hashes, safe refs and server-inlined data sources");
      ids.add(asset.id);
      return Object.freeze({ ...asset });
    });
    const milliseconds = frameToMilliseconds(input.frame, input.fps);
    return Object.freeze({ composition: Object.freeze({ ...input.composition }), fps: Object.freeze({ ...input.fps }), frame: input.frame, seed: input.seed, assets: Object.freeze(assets), seconds: milliseconds / 1e3, milliseconds });
  }
  function evaluateFrame(input, evaluate2) {
    return evaluate2(frameContext(input));
  }

  // core/spring.ts
  function criticalState(displacement, velocity, seconds2, omega) {
    const e = Math.exp(-omega * seconds2), step = (velocity + omega * displacement) * seconds2;
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
    const m = model(options), seconds2 = Math.max(0, frameToMilliseconds(frame, fps)) / 1e3;
    const value = seconds2 === 0 ? m.from : m.to + m.value(seconds2);
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

  // core/timing.ts
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

  // core/layout.ts
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

  // core/stage.ts
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
    for (const p of people) {
      const seat = home.get(p.id);
      for (const r of reserved) {
        if (overlap(personRect(seat, p.quiet, p.hasThumb), r) === 0) continue;
        const below = { x: seat.x, y: r.y + r.h + 26 + 10 };
        const rect = personRect(below, p.quiet, p.hasThumb);
        home.set(p.id, rect.y + rect.h <= stage.y + stage.h - 10 ? below : { x: r.x + r.w + PERSON.w / 2 + 10, y: seat.y });
      }
    }
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

  // core/random.ts
  function random(seed, key) {
    integer(seed, "seed", 0);
    if (seed > 2147483647) throw new RangeError("seed must be in [0, 2^31-1]");
    let h = 2166136261;
    const text = String(key);
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
    h ^= Math.imul(seed ^ 2654435769, 2246822507);
    h = Math.imul(h ^ h >>> 16, 2246822507);
    h = Math.imul(h ^ h >>> 13, 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  }
  function noise(seed, key, t) {
    const i = Math.floor(t), f = t - i, s = f * f * (3 - 2 * f);
    const a = random(seed, `${key}:${i}`) * 2 - 1, b = random(seed, `${key}:${i + 1}`) * 2 - 1;
    return a + (b - a) * s;
  }

  // core/camera.ts
  var IDENTITY = [1, 0, 0, 1, 0, 0];
  function regionView(region, width, height, padding = 0.08) {
    const [x, y, w, h] = region;
    if (!(w > 0 && h > 0)) throw new RangeError("A camera region needs a positive width and height");
    const zoom = Math.min(width / (w * (1 + 2 * padding)), height / (h * (1 + 2 * padding)));
    return { x: x + w / 2, y: y + h / 2, zoom, rotate: 0 };
  }
  function blendViews(a, b, e) {
    const zoom = Math.exp(Math.log(a.zoom) + (Math.log(b.zoom) - Math.log(a.zoom)) * e);
    const rotate = a.rotate + (b.rotate - a.rotate) * e;
    if (Math.abs(a.zoom - b.zoom) <= 1e-3 * Math.max(a.zoom, b.zoom)) {
      return { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e, zoom, rotate };
    }
    const px = (a.zoom * a.x - b.zoom * b.x) / (a.zoom - b.zoom), py = (a.zoom * a.y - b.zoom * b.y) / (a.zoom - b.zoom);
    const k = a.zoom / zoom;
    return { x: px + (a.x - px) * k, y: py + (a.y - py) * k, zoom, rotate };
  }
  function toScreen(view, width, height, p) {
    const r = view.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const dx = (p[0] - view.x) * view.zoom, dy = (p[1] - view.y) * view.zoom;
    return [width / 2 + c * dx - s * dy, height / 2 + s * dx + c * dy];
  }
  function toWorld(view, width, height, p) {
    const r = -view.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const dx = p[0] - width / 2, dy = p[1] - height / 2;
    return [view.x + (c * dx - s * dy) / view.zoom, view.y + (s * dx + c * dy) / view.zoom];
  }
  function screenMotion(prev, now, width, height) {
    let mx = 0, my = 0;
    for (const [fx, fy] of [[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]]) {
      const s = [fx * width, fy * height];
      const before = toScreen(prev, width, height, toWorld(now, width, height, s));
      mx = Math.max(mx, Math.abs(s[0] - before[0]));
      my = Math.max(my, Math.abs(s[1] - before[1]));
    }
    return [mx, my];
  }
  function layerAffine(v) {
    const r = v.rotate * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const a = c * v.sx, b = s * v.sx, cc = -s * v.sy, d = c * v.sy;
    const ox = v.ax * v.w, oy = v.ay * v.h;
    return [a, b, cc, d, v.x - (a * ox + cc * oy), v.y - (b * ox + d * oy)];
  }
  function compose(parent, child) {
    const [a, b, c, d, e, f] = parent, [a2, b2, c2, d2, e2, f2] = child;
    return [a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f];
  }
  function mapRegion(m, region) {
    const [x, y, w, h] = region, [a, b, c, d, e, f] = m;
    const pts = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]].map(([u, v]) => [a * u + c * v + e, b * u + d * v + f]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys);
    return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0];
  }

  // core/cursor.ts
  var STEP_S = 1 / 240;
  var CURSOR_OMEGA = 22;
  var RIPPLE_S = 0.5;
  var ARRIVE_S = 0.3;
  var POINTER_TYPES = /* @__PURE__ */ new Set(["move", "down", "up", "click", "scroll"]);
  var seconds = (frame, fps) => integer(frame, "event frame", 0) * fps.den / fps.num;
  var isPress = (e) => e.type === "click" || e.type === "down";
  function rippleTimes(events, fps) {
    let lastDown = -Infinity;
    const times = [];
    for (const e of events) {
      const t = seconds(e.frame, fps);
      if (e.type === "down") {
        lastDown = t;
        times.push(t);
      } else if (e.type === "click" && t - lastDown >= RIPPLE_S) times.push(t);
    }
    return times;
  }
  function pointerTargets(events, fps) {
    const placed = events.filter((e) => POINTER_TYPES.has(e.type) && typeof e.x === "number" && typeof e.y === "number");
    const targets = [];
    placed.forEach((e, i) => {
      const t = seconds(e.frame, fps), prev = targets[targets.length - 1];
      if (prev && prev.x === e.x && prev.y === e.y) return;
      const press = placed.slice(i).find((p) => p.x !== e.x || p.y !== e.y || isPress(p));
      const pressAt = press && isPress(press) && press.x === e.x && press.y === e.y ? seconds(press.frame, fps) : null;
      const lead = pressAt !== null && pressAt - t < ARRIVE_S ? pressAt - ARRIVE_S : t;
      targets.push({ t: prev ? Math.max(prev.t, lead) : t, x: e.x, y: e.y });
    });
    return targets;
  }
  function cursorTrack(events, fps) {
    const targets = pointerTargets(events, fps);
    const clicks = rippleTimes(events, fps);
    if (!targets.length) return () => ({ x: 0, y: 0, visible: false, clickAge: null });
    const start = targets[0].t, end = targets[targets.length - 1].t + 1;
    const n = Math.ceil((end - start) / STEP_S) + 1;
    const xs = new Float64Array(n), ys = new Float64Array(n);
    let x = targets[0].x, y = targets[0].y, vx = 0, vy = 0, k = 0;
    for (let i = 0; i < n; i++) {
      const t = start + i * STEP_S;
      while (k + 1 < targets.length && targets[k + 1].t <= t) k++;
      if (i) {
        [x, vx] = springStep(x, vx, targets[k].x, STEP_S, CURSOR_OMEGA);
        [y, vy] = springStep(y, vy, targets[k].y, STEP_S, CURSOR_OMEGA);
      }
      xs[i] = x;
      ys[i] = y;
    }
    return (t) => {
      if (t < start) return { x: xs[0], y: ys[0], visible: false, clickAge: null };
      const f = Math.min(n - 1, (t - start) / STEP_S), i = Math.floor(f), j = Math.min(n - 1, i + 1), u = f - i;
      let last = null;
      for (const c of clicks) if (c <= t) last = c;
      else break;
      const age = last === null ? null : t - last;
      return { x: xs[i] + (xs[j] - xs[i]) * u, y: ys[i] + (ys[j] - ys[i]) * u, visible: true, clickAge: age !== null && age < RIPPLE_S ? age : null };
    };
  }
  function followShots(events, fps, viewport, options = {}) {
    const zoom = options.zoom ?? 2.2, idle = (options.idleMs ?? 1200) / 1e3;
    const active = events.filter((e) => e.type !== "move" && e.type !== "up");
    const shots = [];
    let burst = [];
    const close = () => {
      if (!burst.length) return;
      const anchor = burst.find((e) => e.type === "click" || e.type === "down") ?? burst.find((e) => typeof e.x === "number");
      const t0 = seconds(burst[0].frame, fps), t1 = seconds(burst[burst.length - 1].frame, fps);
      if (anchor) {
        const [vw, vh] = viewport, w = vw / zoom, h = vh / zoom;
        const rect = anchor.rect && anchor.rect[2] > 0 && anchor.rect[3] > 0 ? anchor.rect : null;
        const cx = rect ? rect[0] + rect[2] / 2 : anchor.x, cy = rect ? rect[1] + rect[3] / 2 : anchor.y;
        const rw = Math.max(w, rect ? rect[2] : 0), rh = Math.max(h, rect ? rect[3] : 0);
        const x = Math.min(Math.max(0, cx - rw / 2), Math.max(0, vw - rw)), y = Math.min(Math.max(0, cy - rh / 2), Math.max(0, vh - rh));
        shots.push({ start: t0, end: t1 + idle, region: [x, y, Math.min(rw, vw), Math.min(rh, vh)] });
      }
      burst = [];
    };
    for (const e of active) {
      if (burst.length && seconds(e.frame, fps) - seconds(burst[burst.length - 1].frame, fps) > idle) close();
      burst.push(e);
    }
    close();
    return shots;
  }

  // core/timemap.ts
  var cache = /* @__PURE__ */ new WeakMap();
  var number = (v, fallback) => typeof v === "number" && Number.isFinite(v) ? v : fallback;
  function timeMap(layer, frames, props, fps) {
    const key = `${frames}|${fps.num}/${fps.den}`;
    const known = cache.get(layer)?.get(props)?.get(key);
    if (known) return known;
    const step = fps.den / fps.num, base = number(sampleField(layer, "rate", 0, props, fps), 1);
    const trim = number(sampleField(layer, "trim", 0, props, fps), 0) * step;
    const sums = new Float64Array(frames + 1);
    sums[0] = trim;
    for (let f = 0; f < frames; f++) {
      const a = number(sampleField(layer, "speed", f, props, fps), base), b = number(sampleField(layer, "speed", f + 1, props, fps), base);
      sums[f + 1] = sums[f] + (Math.max(0, a) + Math.max(0, b)) / 2 * step;
    }
    const map = {
      seconds(contentFrame) {
        if (contentFrame <= 0) return sums[0] + contentFrame * base * step;
        if (contentFrame >= frames) return sums[frames] + (contentFrame - frames) * base * step;
        const i = Math.floor(contentFrame), u = contentFrame - i;
        return sums[i] + (sums[i + 1] - sums[i]) * u;
      },
      frameAt(seconds2) {
        if (seconds2 <= sums[0]) return 0;
        if (seconds2 >= sums[frames]) return frames;
        let lo = 0, hi = frames;
        while (hi - lo > 1) {
          const mid = lo + hi >> 1;
          if (sums[mid] < seconds2) lo = mid;
          else hi = mid;
        }
        const span = sums[hi] - sums[lo];
        return lo + (span > 0 ? (seconds2 - sums[lo]) / span : 0);
      }
    };
    let byProps = cache.get(layer);
    if (!byProps) cache.set(layer, byProps = /* @__PURE__ */ new WeakMap());
    let byKey = byProps.get(props);
    if (!byKey) byProps.set(props, byKey = /* @__PURE__ */ new Map());
    byKey.set(key, map);
    return map;
  }

  // core/memo.ts
  var NO_PROPS = Object.freeze({});
  function pairCache(compute) {
    const outer = /* @__PURE__ */ new WeakMap();
    return (a, b) => {
      let inner = outer.get(a);
      if (!inner) outer.set(a, inner = /* @__PURE__ */ new WeakMap());
      if (!inner.has(b)) inner.set(b, compute(a, b));
      return inner.get(b);
    };
  }

  // core/scene.ts
  var SPRING_PRESETS = {
    "spring-soft": { stiffness: 110, damping: 18 },
    "spring-snappy": { stiffness: 320, damping: 30 }
  };
  var META = /* @__PURE__ */ new Set(["id", "type", "name", "hidden", "keyframes", "layers"]);
  var isProp = (v) => !!v && typeof v === "object" && typeof v.prop === "string";
  function segmentEasing(spec, fps, length) {
    if (spec === void 0) return easing.linear;
    if (typeof spec === "string" && spec in SPRING_PRESETS) return segmentEasing({ spring: SPRING_PRESETS[spec] }, fps, length);
    if (typeof spec === "string") {
      const curve = easing[spec];
      if (typeof curve !== "function" || spec === "cubicBezier") throw new RangeError(`Unknown easing "${spec}"`);
      return curve;
    }
    if ("bezier" in spec) return cubicBezier(...spec.bezier);
    const options = { ...spec.spring, from: 0, to: 1 };
    const settle = measureSpring(fps, options);
    if (!Number.isFinite(settle) || settle <= 0 || length <= 0) return easing.linear;
    const stretched = { num: fps.num * length, den: fps.den * settle };
    return (progress) => progress >= 1 ? 1 : progress <= 0 ? 0 : spring(Math.round(progress * length), stretched, options);
  }
  function sampleField(layer, name, frame, props, fps) {
    const bound = (v) => isProp(v) ? props[v.prop] : v;
    const points = layer.keyframes?.[name];
    if (!points?.length) return bound(layer[name]);
    if (frame <= points[0].frame) return bound(points[0].value);
    const last = points[points.length - 1];
    if (frame >= last.frame) return bound(last.value);
    let i = 1;
    while (points[i].frame <= frame) i++;
    const a = bound(points[i - 1].value), b = bound(points[i].value);
    const range = [points[i - 1].frame, points[i].frame];
    const ease = segmentEasing(points[i].easing, fps, range[1] - range[0]);
    if (typeof a === "number" && typeof b === "number") return interpolate(frame, range, [a, b], { easing: ease });
    if (typeof a === "string" && typeof b === "string" && a.startsWith("#") && b.startsWith("#")) return interpolateColors(frame, range, [a, b], { easing: ease });
    return a;
  }
  function drawLayers(layers, frame, props, fps) {
    const out = [];
    for (const layer of layers) {
      if (layer.hidden) continue;
      const values = {};
      const names = /* @__PURE__ */ new Set([...Object.keys(layer).filter((k) => !META.has(k)), ...Object.keys(layer.keyframes ?? {})]);
      for (const name of names) values[name] = sampleField(layer, name, frame, props, fps);
      const item = { id: layer.id, type: layer.type, values };
      if (layer.type === "group") item.children = drawLayers(layer.layers ?? [], frame, props, fps);
      out.push(item);
    }
    return out;
  }
  function evaluate(composition, frame, overrides = NO_PROPS) {
    validateFps(composition.fps);
    if (!Number.isSafeInteger(frame) || frame < 0 || frame >= composition.duration) throw new RangeError(`frame must be an integer in [0, ${composition.duration - 1}]`);
    const props = compositionProps(composition, overrides);
    const entering = (clip) => {
      const t = clip.transition, local = frame - clip.from;
      if (!t || t.kind === "cut" || t.duration <= 0 || local < 0 || local >= t.duration) return null;
      return { kind: t.kind, direction: t.direction ?? "left", progress: segmentEasing(t.easing ?? "ease-in-out", composition.fps, t.duration)(local / t.duration) };
    };
    const clips = [];
    for (const track of composition.tracks) {
      if (track.hidden || track.kind !== "visual") continue;
      const ordered = [...track.clips].sort((a, b) => a.from - b.from);
      ordered.forEach((clip, i) => {
        const local = frame - clip.from;
        if (local < 0 || local >= clip.duration) return;
        const next = ordered.slice(i + 1).find((c) => c.from < clip.from + clip.duration && frame >= c.from);
        clips.push({
          id: clip.id,
          track: track.id,
          frame: local,
          contentFrame: local + (clip.offset ?? 0),
          duration: clip.duration,
          transition: entering(clip),
          exit: next ? entering(next) : null,
          fixed: !!track.fixed,
          layers: drawLayers(clip.layers, local, props, composition.fps)
        });
      });
    }
    const camera = cameraAt(composition, frame, props), cameraPrev = frame > 0 ? cameraAt(composition, frame - 1, props) : camera;
    return { frame, width: composition.width, height: composition.height, background: composition.background ?? "#0c0e10", clips, camera, cameraPrev };
  }
  var MEDIA = /* @__PURE__ */ new Set(["image", "capture", "video", "ui"]);
  var num = (v, fallback) => typeof v === "number" && Number.isFinite(v) ? v : fallback;
  function layerToComposition(composition, layerId, frame, props = compositionProps(composition)) {
    const W = composition.width, H = composition.height;
    const box = (layer, local) => {
      const v = (name) => sampleField(layer, name, local, props, composition.fps);
      const media = MEDIA.has(layer.type), s = num(v("scale"), 1);
      const w = num(v("width"), media ? W : 0), h = num(v("height"), media ? H : 0);
      return { w, h, m: layerAffine({ x: num(v("x"), 0), y: num(v("y"), 0), w, h, ax: num(v("anchor_x"), 0), ay: num(v("anchor_y"), 0), rotate: num(v("rotate"), 0), sx: s * num(v("scale_x"), 1), sy: s * num(v("scale_y"), 1) }) };
    };
    const find = (layers, local, parent) => {
      for (const layer of layers) {
        const b = box(layer, local), m = compose(parent, b.m);
        if (layer.id === layerId) return { matrix: m, size: [b.w, b.h] };
        if (layer.type === "group") {
          const hit = find(layer.layers ?? [], local, m);
          if (hit) return hit;
        }
      }
      return null;
    };
    for (const track of composition.tracks) for (const clip of track.clips) {
      const local = Math.min(clip.duration - 1, Math.max(0, frame - clip.from));
      const hit = find(clip.layers, local, IDENTITY);
      if (hit) return hit;
    }
    throw new RangeError(`camera region names unknown layer "${layerId}"`);
  }
  var HOME = (c) => ({ x: c.width / 2, y: c.height / 2, zoom: 1, rotate: 0 });
  function cameraKeyView(composition, key, props = compositionProps(composition)) {
    let view = HOME(composition);
    if (key.region) {
      const region = key.layer ? mapRegion(layerToComposition(composition, key.layer, key.frame, props).matrix, key.region) : key.region;
      view = regionView(region, composition.width, composition.height, key.padding ?? 0.08);
    }
    return { x: key.x ?? view.x, y: key.y ?? view.y, zoom: key.zoom ?? view.zoom, rotate: key.rotate ?? view.rotate };
  }
  function cameraAt(composition, frame, props = compositionProps(composition)) {
    const keys = cameraKeys(composition, props);
    if (!keys.length) return HOME(composition);
    if (frame <= keys[0].frame) return cameraKeyView(composition, keys[0], props);
    const last = keys[keys.length - 1];
    if (frame >= last.frame) return cameraKeyView(composition, last, props);
    let i = 1;
    while (keys[i].frame <= frame) i++;
    const a = keys[i - 1], b = keys[i], length = b.frame - a.frame;
    const e = segmentEasing(b.easing ?? "premium", composition.fps, length)((frame - a.frame) / length);
    return blendViews(cameraKeyView(composition, a, props), cameraKeyView(composition, b, props), e);
  }
  var FOLLOW = { leadS: 0.7, arriveS: 0.1, releaseS: 0.8 };
  var cameraKeys = pairCache((composition, props) => {
    const explicit = composition.camera?.keyframes ?? [];
    if (!composition.camera?.follow) return explicit;
    const generated = followKeys(composition, props);
    if (!explicit.length) return generated;
    const first = explicit[0].frame, last = explicit[explicit.length - 1].frame;
    return [...generated.filter((k) => k.frame < first || k.frame > last), ...explicit].sort((a, b) => a.frame - b.frame);
  });
  var compositionProps = (() => {
    const merge = pairCache((composition, overrides) => Object.freeze({ ...composition.props ?? {}, ...overrides }));
    return (composition, overrides = NO_PROPS) => merge(composition, overrides);
  })();
  function followKeys(composition, props = compositionProps(composition)) {
    const follow = composition.camera?.follow;
    if (!follow) return [];
    const fps = composition.fps, toCompFrames = (seconds2) => seconds2 * fps.num / fps.den;
    for (const track of composition.tracks) for (const clip of track.clips) {
      const all = [];
      const walk = (layers) => layers.forEach((l) => {
        all.push(l);
        if (l.layers) walk(l.layers);
      });
      walk(clip.layers);
      const cursor = all.find((l) => l.id === follow.cursor && l.type === "cursor");
      if (!cursor) continue;
      const source = all.find((l) => l.id === cursor.source && l.type === "video");
      if (!source) throw new RangeError(`cursor "${cursor.id}" needs its source video in the same clip`);
      const recFps = cursor.fps ?? fps;
      const boxW = num(source.width, composition.width), boxH = num(source.height, composition.height);
      const viewport = cursor.viewport ?? [boxW, boxH];
      const k = boxW / viewport[0];
      const offset = clip.offset ?? 0;
      const clock = timeMap(source, clip.duration + offset, props, fps);
      const frameOf = (t) => clip.from + clock.frameAt(t) - offset;
      const shown = [clock.seconds(offset), clock.seconds(offset + clip.duration)];
      const shots = followShots(cursor.events, recFps, viewport, { zoom: follow.zoom, idleMs: follow.idle_ms }).filter((shot) => shot.end > shown[0] && shot.start < shown[1]);
      const lo = clip.from, hi = clip.from + clip.duration - 1, clampF = (f) => Math.round(Math.min(hi, Math.max(lo, f)));
      const home = (frame) => ({ frame: clampF(frame), x: composition.width / 2, y: composition.height / 2, zoom: 1, rotate: 0, easing: "premium" });
      const keys = [];
      shots.forEach((shot, i) => {
        const region = [shot.region[0] * k, shot.region[1] * k, shot.region[2] * k, shot.region[3] * k];
        const arrive = frameOf(shot.start) - toCompFrames(FOLLOW.arriveS), push = frameOf(shot.start) - toCompFrames(FOLLOW.leadS);
        const next = shots[i + 1], nextPush = next ? frameOf(next.start) - toCompFrames(FOLLOW.leadS) : Infinity;
        const release = nextPush > frameOf(shot.end) + toCompFrames(FOLLOW.releaseS);
        const hold = release ? frameOf(shot.end) : Math.min(frameOf(shot.end), nextPush);
        const joined = keys.length > 0 && keys[keys.length - 1].region !== void 0;
        if (!joined) keys.push(home(push));
        keys.push({ frame: clampF(arrive), layer: source.id, region, padding: 0.04, easing: "premium" });
        keys.push({ frame: clampF(Math.max(arrive, hold)), layer: source.id, region, padding: 0.04, easing: "premium" });
        if (release) keys.push(home(hold + toCompFrames(FOLLOW.releaseS)));
      });
      for (let i = 1; i < keys.length; i++) if (keys[i].frame < keys[i - 1].frame) keys[i] = { ...keys[i], frame: keys[i - 1].frame };
      return keys;
    }
    throw new RangeError(`camera follows unknown cursor layer "${follow.cursor}"`);
  }

  // core/audio.ts
  var AUDIO_PLAN_VERSION = 1;
  var AUDIBLE = /* @__PURE__ */ new Set(["audio", "video", "voiceover"]);
  var number2 = (v, fallback) => typeof v === "number" && Number.isFinite(v) ? v : fallback;
  function audioPlan(composition, overrides = NO_PROPS) {
    const props = compositionProps(composition, overrides);
    const field = (layer, name, frame = 0) => sampleField(layer, name, frame, props, composition.fps);
    const sounds = [];
    for (const track of composition.tracks) {
      if (track.hidden || track.muted) continue;
      for (const clip of track.clips) {
        const visit = (layers) => {
          for (const layer of layers) {
            if (layer.hidden) continue;
            if (layer.type === "group") {
              visit(layer.layers ?? []);
              continue;
            }
            if (!AUDIBLE.has(layer.type)) continue;
            if (layer.type === "video" && field(layer, "muted") === true) continue;
            const volume = Array.from({ length: clip.duration + 1 }, (_, f) => Math.min(1, Math.max(0, number2(field(layer, "volume", Math.min(f, clip.duration - 1)), 1))));
            if (layer.type === "video" && volume.every((v) => v === 0)) continue;
            if (layer.type === "video" && ("speed" in layer || "speed" in (layer.keyframes ?? {}))) {
              throw new RangeError(`video "${layer.id}" has speed, which its sound can't follow: set muted true`);
            }
            const sound = {
              id: layer.id,
              type: layer.type,
              track: track.id,
              clip: { id: clip.id, from: clip.from, duration: clip.duration, offset: clip.offset ?? 0 },
              trim: number2(field(layer, "trim"), 0),
              rate: number2(field(layer, "rate"), 1),
              loop: field(layer, "loop") === true,
              volume
            };
            const optional = { asset: field(layer, "asset"), text: field(layer, "text"), voice: field(layer, "voice"), role: field(layer, "role"), duck: layer.duck };
            for (const [key, value] of Object.entries(optional)) if (value !== void 0) sound[key] = value;
            sounds.push(sound);
          }
        };
        visit(clip.layers);
      }
    }
    return { version: AUDIO_PLAN_VERSION, sounds };
  }
  return __toCommonJS(index_exports);
})();
