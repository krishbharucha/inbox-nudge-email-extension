import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve, sep } from 'node:path';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'NUDGE_');
  const origin = env.NUDGE_API_ORIGIN || 'http://127.0.0.1:8000';
  const outDir = env.NUDGE_BUILD_DIR || 'dist/extension';
  const url = new URL(origin);
  if (
    url.origin !== origin ||
    (url.protocol !== 'https:' &&
      !(url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname)))
  )
    throw new Error('NUDGE_API_ORIGIN must be an HTTPS origin, or HTTP on loopback.');
  if (!resolve(outDir).startsWith(resolve('dist') + sep))
    throw new Error('Build output must be inside dist/.');
  return {
    plugins: [react(), tailwindcss()],
    define: { __NUDGE_API_ORIGIN__: JSON.stringify(origin) },
    server: { proxy: { '/api': origin } },
    build: { outDir },
  };
});
