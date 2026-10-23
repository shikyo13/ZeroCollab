/** Build the render-only product entry. Module substitutions never affect the
 * app bundle; no network/store bridge is included in the UI composition bundle. */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const tool = path.resolve(here, '../..');
const require = createRequire(path.join(tool, 'package.json'));
const { build } = require('esbuild');
if (!process.argv[2]) throw new Error('Pass the explicit ZeroCollab checkout: npm run build:product-adapter -- <checkout>');
const product = path.resolve(process.argv[2]);
const src = path.join(product, 'apps/desktop/renderer/src');
const ui = path.join(here, 'product-ui');
const kit = path.join(tool, 'src/zerocollab_motion/kit');
// CSS travels through page builders on Windows too. Preserve Unicode through
// ASCII CSS escapes rather than depending on each consumer's default encoding.
const asciiCss = text => text.replace(/[^\x00-\x7f]/gu, char => `\\${char.codePointAt(0).toString(16)} `);
await fs.mkdir(kit, { recursive: true });
const result = await build({
  entryPoints: [path.join(ui, 'index.tsx')], bundle: true, write: false, format: 'iife',
  target: 'chrome120', jsx: 'automatic', minify: true, legalComments: 'none',
  nodePaths: [path.join(product, 'apps/desktop/renderer/node_modules')],
  define: { 'process.env.NODE_ENV': '"production"' },
  outfile: path.join(kit, 'ui.js'),
  plugins: [{ name: 'scripted-product-bridges', setup(builder) {
    builder.onResolve({ filter: /(?:^|\/)followStore$/ }, () => ({ path: path.join(ui, 'scriptedFollow.ts') }));
    builder.onResolve({ filter: /(?:^|\/)room$/ }, () => ({ path: path.join(ui, 'scriptedRoom.ts') }));
    builder.onResolve({ filter: /SpeechBubble$/ }, args => {
      if (path.basename(args.importer) === 'Follow.tsx') return { path: path.join(ui, 'SpeechAdapter.tsx') };
    });
    builder.onResolve({ filter: /^\.\.\/\.\.\/\.\.\/motion\/core\// }, args => ({
      path: path.join(kit, 'core', path.basename(args.path) + '.ts'),
    }));
    builder.onResolve({ filter: /^\.\.\/(?:board|follow|components|lib)\// }, args => {
      if (path.dirname(args.importer) === ui)
        return builder.resolve('./' + args.path.slice(3), { resolveDir: src, kind: args.kind });
    });
  } }],
});
for (const output of result.outputFiles) {
  if (output.path.endsWith('.js')) await fs.writeFile(path.join(kit, 'ui.js'), output.contents);
  else if (output.path.endsWith('.css')) {
    const tokens = (await fs.readFile(path.join(src, 'styles.css'), 'utf8')).replaceAll('body.modern-workspace', '.ui-product');
    const board = await fs.readFile(path.join(src, 'board/board.css'), 'utf8');
    await fs.writeFile(path.join(kit, 'ui.css'), asciiCss(`${tokens}\n${board}\n${output.text}`));
  }
}
console.log('Built kit/ui.js and kit/ui.css from the explicit product checkout');
