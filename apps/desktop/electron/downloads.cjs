const path = require('node:path');
const images = new Map([
  ['.png', 'image/png'], ['.jpg', 'image/jpeg'], ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'], ['.webp', 'image/webp'], ['.bmp', 'image/bmp'], ['.svg', 'image/svg+xml'],
]);
const forbidden = new Set('exe dll com scr bat cmd ps1 vbs js jse wsf hta msi lnk url reg jar'.split(' '));
function safeName(value) {
  let name = String(value).replace(/[\\/<>:"|?*\x00-\x1f\x7f]/g, '_').replace(/[ .]+$/g, '');
  const ext = path.extname(name);
  if (name.length > 180) name = name.slice(0, Math.max(1, 180 - ext.length)) + ext;
  if (!name || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) return null;
  return name;
}
function allowedDownload(origin, chain, filename, mime) {
  const name = safeName(filename);
  if (!name || forbidden.has(path.extname(name).slice(1).toLowerCase()) || !chain.length) return null;
  const ext = path.extname(name).toLowerCase();
  let kind;
  for (const entry of chain) {
    let u; try { u = new URL(entry); } catch { return null; }
    let current;
    if (u.protocol === 'blob:' && u.origin === origin) current = ext === '.webm' && mime === 'video/webm' ? 'video' : 'export';
    else if (u.origin === origin && u.pathname === '/api/export') current = 'export';
    else if (u.origin === origin && /^\/api\/runtime\/motion\/exports\/[a-f0-9]{12}\/video$/.test(u.pathname)) current = 'video';
    else if (u.origin === origin && /^\/uploads\/[^/]+$/.test(u.pathname)) current = 'image';
    else return null;
    if (kind && kind !== current) return null;
    kind = current;
  }
  if (kind === 'export') return ext === '.zip' && ['application/zip', 'application/x-zip-compressed'].includes(mime) ? name : null;
  if (kind === 'video') return ext === '.webm' && mime === 'video/webm' ? name : null;
  return images.get(ext) === mime ? name : null;
}
module.exports = { safeName, allowedDownload };
