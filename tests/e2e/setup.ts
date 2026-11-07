import { execFileSync } from 'node:child_process';

export default function setup() {
  execFileSync('npm', ['run', 'build'], {
    env: {
      ...process.env,
      NUDGE_API_ORIGIN: 'http://127.0.0.1:8011',
      NUDGE_BUILD_DIR: 'dist/e2e-extension',
    },
    stdio: 'inherit',
  });
}
