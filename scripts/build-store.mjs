import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { isIP } from 'node:net';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadEnv } from 'vite';

export function storeConfig(env) {
  let url;
  try {
    url = new URL(env.NUDGE_API_ORIGIN);
  } catch {
    throw new Error(
      'Set NUDGE_API_ORIGIN to the real public HTTPS backend origin before building for the Store.',
    );
  }
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  const reserved = [
    'localhost',
    'local',
    'internal',
    'test',
    'invalid',
    'example',
    'example.com',
    'example.net',
    'example.org',
  ];
  if (
    url.protocol !== 'https:' ||
    url.origin !== env.NUDGE_API_ORIGIN ||
    isIP(host) ||
    !host.includes('.') ||
    reserved.some((value) => host === value || host.endsWith('.' + value))
  )
    throw new Error(
      'Store builds require a public HTTPS origin; localhost, IP addresses, credentials and placeholder domains are not accepted.',
    );
  if (!/^[a-p]{32}$/.test(env.NUDGE_STORE_EXTENSION_ID ?? ''))
    throw new Error(
      'Set NUDGE_STORE_EXTENSION_ID to the item ID from an unpublished Chrome Web Store draft.',
    );
  return { apiOrigin: url.origin, extensionId: env.NUDGE_STORE_EXTENSION_ID };
}

export async function verifyBackend(config, fetcher = fetch) {
  const response = await fetcher(config.apiOrigin + '/api/health', {
    signal: AbortSignal.timeout(10000),
    redirect: 'error',
  });
  if (!response.ok) throw new Error('The public backend health check failed.');
  const health = await response.json();
  if (
    health.status !== 'ok' ||
    health.deployment_environment !== 'production' ||
    health.demo_mode !== false ||
    health.google_configured !== true ||
    health.extension_id !== config.extensionId ||
    health.request_limits_enabled !== true
  )
    throw new Error(
      'The backend must run in production mode with Google configured, demo login disabled, request limits enabled, and the matching Store item ID.',
    );
}

async function main() {
  const root = process.cwd();
  const config = storeConfig(loadEnv('production', root, 'NUDGE_'));
  if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0')
    throw new Error('Re-enable TLS certificate verification before creating a release.');
  await verifyBackend(config);
  const env = {
    ...process.env,
    NUDGE_API_ORIGIN: config.apiOrigin,
    NUDGE_BUILD_DIR: 'dist/store-extension',
  };
  for (const [script, args] of [
    ['node_modules/typescript/bin/tsc', ['--noEmit']],
    ['node_modules/vite/bin/vite.js', ['build']],
    ['scripts/build-extension.mjs', []],
  ])
    execFileSync(process.execPath, [resolve(script), ...args], { env, stdio: 'inherit' });
  const manifest = JSON.parse(await readFile('dist/store-extension/manifest.json', 'utf8'));
  if (
    manifest.host_permissions.length !== 1 ||
    manifest.host_permissions[0] !== config.apiOrigin + '/*'
  )
    throw new Error('Release manifest origin does not match the verified backend.');
  const zip = `dist/inbox-nudge-store-${manifest.version}.zip`;
  execFileSync(
    resolve('.venv/bin/python'),
    ['scripts/package-extension.py', 'dist/store-extension', zip],
    { stdio: 'inherit' },
  );
  const digest = createHash('sha256')
    .update(await readFile(zip))
    .digest('hex');
  await writeFile(
    'dist/store-release.json',
    JSON.stringify(
      {
        ...config,
        version: manifest.version,
        zip,
        sha256: digest,
        built_at: new Date().toISOString(),
        google_handoff_uri: `https://${config.extensionId}.chromiumapp.org/google`,
      },
      null,
      2,
    ) + '\n',
  );
  console.log(`Verified Store package: ${zip}\nSHA-256: ${digest}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
