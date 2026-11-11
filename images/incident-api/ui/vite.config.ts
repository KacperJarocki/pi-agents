import { resolve } from 'node:path';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// Two entry pages share one bundle: `/` (devices) and `/live/` (incidents),
// so a reload on either URL works with plain StaticFiles in incident-api.
// `npm run dev` proxies the API to a local incident-api on :8080.
const api = process.env.API_URL ?? 'http://localhost:8080';

export default defineConfig({
  plugins: [svelte()],
  build: {
    target: 'es2022',
    rollupOptions: {
      input: {
        devices: resolve(import.meta.dirname, 'index.html'),
        live: resolve(import.meta.dirname, 'live/index.html'),
      },
    },
  },
  server: {
    proxy: {
      '/api': api,
      '/ws': { target: api, ws: true },
    },
  },
  // Playwright mocks the API in the browser; preview serves only the build.
  preview: { proxy: {} },
  test: { include: ['src/**/*.test.ts'] },
});
