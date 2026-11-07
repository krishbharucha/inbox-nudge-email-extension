import { describe, expect, it } from 'vitest';
import { storeConfig, verifyBackend } from '../scripts/build-store.mjs';

const extensionId = 'a'.repeat(32);
describe('Chrome Web Store release gate', () => {
  it.each([
    undefined,
    'http://127.0.0.1:8000',
    'https://localhost',
    'https://127.0.0.1',
    'https://api.example.com',
    'https://service.internal',
    'https://user:password@api.inboxnudge.dev',
    'https://api.inboxnudge.dev/path',
  ])('rejects a development, private, placeholder or malformed origin: %s', (origin) => {
    expect(() =>
      storeConfig({ NUDGE_API_ORIGIN: origin, NUDGE_STORE_EXTENSION_ID: extensionId }),
    ).toThrow();
  });
  it('requires the assigned Store item ID and accepts a public origin', () => {
    expect(() => storeConfig({ NUDGE_API_ORIGIN: 'https://api.inboxnudge.dev' })).toThrow(
      'item ID',
    );
    expect(
      storeConfig({
        NUDGE_API_ORIGIN: 'https://api.inboxnudge.dev',
        NUDGE_STORE_EXTENSION_ID: extensionId,
      }),
    ).toEqual({ apiOrigin: 'https://api.inboxnudge.dev', extensionId });
  });
  it('rejects a development backend or mismatched Store ID', async () => {
    const config = { apiOrigin: 'https://api.inboxnudge.dev', extensionId };
    const fetcher: typeof fetch = async () =>
      new Response(
        JSON.stringify({
          status: 'ok',
          deployment_environment: 'development',
          demo_mode: true,
          google_configured: true,
          extension_id: extensionId,
          request_limits_enabled: true,
        }),
      );
    await expect(verifyBackend(config, fetcher)).rejects.toThrow('production mode');
  });
  it('validates the public backend before any package is built', async () => {
    const config = { apiOrigin: 'https://api.inboxnudge.dev', extensionId };
    const fetcher: typeof fetch = async (url, init) => {
      expect(url).toBe(config.apiOrigin + '/api/health');
      expect(init?.redirect).toBe('error');
      return new Response(
        JSON.stringify({
          status: 'ok',
          deployment_environment: 'production',
          demo_mode: false,
          google_configured: true,
          extension_id: extensionId,
          request_limits_enabled: true,
        }),
      );
    };
    await expect(verifyBackend(config, fetcher)).resolves.toBeUndefined();
  });
});
