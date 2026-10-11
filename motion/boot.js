// Scene page bootstrap. The page carries two JSON data blocks written by motion_studio.view_html():
// zm-options { render, reduced, embed, t } and zm-scene (the validated scene). Both are parsed as
// data only, and the page reads nothing from its URL, so it works the same in a srcdoc frame and
// from a file.
(function () {
  const fail = err => {
    const box = document.createElement('div');
    box.className = 'zm-error';
    box.textContent = 'Scene failed: ' + (err && err.message || err);
    document.body.append(box);
    window.__error = box.textContent;
  };
  const data = id => {
    const block = document.getElementById(id);
    return block ? JSON.parse(block.textContent) : null;
  };
  try {
    const opts = data('zm-options') || {};
    const def = data('zm-scene');
    if (!def) throw new Error('no scene was provided');
    const reduced = opts.reduced === true;
    if (opts.render) document.body.classList.add('render');
    const m = ZCMotion.create(document.getElementById('stage'), { duration: def.duration_ms, reduced });
    m.play(def.beats, ZCMotion.layout(m, def.layout));
    window.__scene = { version: ZCMotion.VERSION, id: def.id, duration: def.duration_ms, reduced, beats: def.beats };
    window.__seek = m.seek;
    if (opts.render) {
      m.seek(0);
    } else {
      const frame = document.getElementById('frame');
      const fit = () => {
        const k = Math.min(innerWidth / 1920, (innerHeight - 64) / 1080);
        frame.style.transform = `scale(${k})`;
        frame.style.height = 1080 * k + 'px';
      };
      fit();
      addEventListener('resize', fit);
      ZCMotion.player(m, document.body, { embed: opts.embed === true, t: Number(opts.t) || 0 });
    }
    document.fonts.ready.then(() => { window.__ready = true; });
  } catch (err) {
    fail(err);
  }
})();
