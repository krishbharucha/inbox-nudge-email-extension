import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { loadEnv } from 'vite';
import { resolve } from 'node:path';

const configured =
  loadEnv('production', process.cwd(), 'NUDGE_').NUDGE_API_ORIGIN || 'http://127.0.0.1:8000';
const url = new URL(configured);
if (
  url.origin !== configured ||
  (url.protocol !== 'https:' &&
    !(url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname)))
)
  throw new Error('NUDGE_API_ORIGIN must be an HTTPS origin, or HTTP on loopback.');
const define = { __NUDGE_API_ORIGIN__: JSON.stringify(url.origin) };
const outDir = loadEnv('production', process.cwd(), 'NUDGE_').NUDGE_BUILD_DIR || 'dist/extension';
if (!resolve(outDir).startsWith(resolve('dist') + '/'))
  throw new Error('Build output must be inside dist/.');

await build({
  entryPoints: ['extension/background.ts'],
  bundle: true,
  outfile: `${outDir}/background.js`,
  format: 'esm',
  target: 'chrome120',
  define,
});
await build({
  entryPoints: ['extension/content.ts'],
  bundle: true,
  outfile: `${outDir}/content.js`,
  format: 'iife',
  target: 'chrome120',
  define,
});
const manifest = JSON.parse(await readFile('extension/manifest.json', 'utf8'));
manifest.host_permissions = [`${url.origin}/*`];
manifest.content_security_policy.extension_pages += `; connect-src 'self' ${url.origin}`;
await writeFile(`${outDir}/manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
