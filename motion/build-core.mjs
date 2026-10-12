import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(root, '../frontend/package.json'));
const { build } = require('esbuild');
await build({ entryPoints: [path.join(root, 'core/index.ts')], outfile: path.join(root, 'core.js'), bundle: true,
  format: 'iife', globalName: 'ZCMotionCore', platform: 'browser', target: 'es2022', charset: 'ascii',
  legalComments: 'none', banner: { js: '// Generated from motion/core. Run node motion/build-core.mjs; edit the TypeScript source.' } });
