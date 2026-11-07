import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const directory = mkdtempSync(join(tmpdir(), 'inbox-nudge-e2e-'));
const python = resolve('.venv/bin/python');
const child = spawn(
  python,
  ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8011', '--no-access-log'],
  {
    cwd: resolve('backend'),
    stdio: 'inherit',
    env: {
      ...process.env,
      DATABASE_URL: `sqlite:///${join(directory, 'test.db')}`,
      JWT_SECRET: randomBytes(48).toString('base64url'),
      TOKEN_ENCRYPTION_KEY: randomBytes(32).toString('base64'),
      DEMO_MODE: 'true',
      APP_ENV: 'development',
      WEB_AUTH_ENABLED: 'true',
      REQUEST_LIMITS_ENABLED: 'false',
      DELIVERY_WORKER_ENABLED: 'false',
      VAPID_PRIVATE_KEY: '',
      VAPID_PUBLIC_KEY: '',
      GOOGLE_CLIENT_ID: '',
      GOOGLE_CLIENT_SECRET: '',
      EXTENSION_ID: '',
      WEB_APP_ORIGIN: 'http://127.0.0.1:5174',
      GOOGLE_REDIRECT_URI: 'http://127.0.0.1:8011/api/auth/google/callback',
      CORS_ORIGINS: 'http://127.0.0.1:5174',
    },
  },
);
child.on('error', (error) => {
  console.error(error.message);
  rmSync(directory, { recursive: true, force: true });
  process.exit(1);
});
child.on('exit', (code) => {
  rmSync(directory, { recursive: true, force: true });
  process.exit(code ?? 0);
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
