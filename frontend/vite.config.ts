import { defineConfig } from 'vite';

export default defineConfig({
  base: '/static/modern/',
  build: {
    outDir: '../static/modern',
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: 'src/main.tsx',
      output: { entryFileNames: 'workspace.js', assetFileNames: 'workspace.[ext]' },
    },
  },
});
