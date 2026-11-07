import { beforeEach, describe, expect, it, vi } from 'vitest';

function area() {
  const data: Record<string, unknown> = {};
  return {
    data,
    setAccessLevel: vi.fn(async () => undefined),
    get: vi.fn(async (keys: string | string[] | null) =>
      keys === null
        ? { ...data }
        : Object.fromEntries(
            (typeof keys === 'string' ? [keys] : keys)
              .filter((key) => key in data)
              .map((key) => [key, data[key]]),
          ),
    ),
    set: vi.fn(async (values: Record<string, unknown>) => {
      Object.assign(data, values);
    }),
    remove: vi.fn(async (keys: string | string[]) => {
      for (const key of typeof keys === 'string' ? [keys] : keys) delete data[key];
    }),
    clear: vi.fn(async () => {
      for (const key of Object.keys(data)) delete data[key];
    }),
  };
}

const local = area(),
  session = area();
const tokens = {
  access_token: 'new-app-access-token',
  refresh_token: 'rotated-app-refresh-token',
  expires_in: 3600,
};
const installation = '11111111-1111-4111-8111-111111111111';
const response = (status = 200) => new Response(JSON.stringify(tokens), { status });

beforeEach(async () => {
  vi.resetModules();
  vi.restoreAllMocks();
  await local.clear();
  await session.clear();
  vi.stubGlobal('__NUDGE_API_ORIGIN__', 'https://api.example.test');
  vi.stubGlobal('chrome', { storage: { local, session } });
  local.data.connection = { refreshToken: 'old-app-refresh-token', installationId: installation };
});

describe('extension application sessions', () => {
  it('restricts both storage areas to trusted contexts and rotates once for concurrent requests', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response());
    vi.stubGlobal('fetch', fetch);
    const { accessToken } = await import('../extension/session');
    expect(await Promise.all(Array.from({ length: 10 }, () => accessToken()))).toEqual(
      Array(10).fill(tokens.access_token),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(local.setAccessLevel).toHaveBeenCalledWith({ accessLevel: 'TRUSTED_CONTEXTS' });
    expect(session.setAccessLevel).toHaveBeenCalledWith({ accessLevel: 'TRUSTED_CONTEXTS' });
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({
      refresh_token: 'old-app-refresh-token',
      installation_id: installation,
    });
    expect(local.data.connection).toEqual({
      refreshToken: tokens.refresh_token,
      installationId: installation,
    });
    expect(session.data.token).toBe(tokens.access_token);
    expect(await accessToken()).toBe(tokens.access_token);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('renews after a browser restart using an application credential, without Google tokens', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response());
    vi.stubGlobal('fetch', fetch);
    const { accessToken } = await import('../extension/session');
    expect(await accessToken()).toBe(tokens.access_token);
    expect(fetch.mock.calls[0][0]).toBe('https://api.example.test/api/auth/extension/refresh');
    expect(JSON.stringify(local.data)).not.toContain('google');
  });

  it('removes revoked credentials and never retries a rejected refresh', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response(401));
    vi.stubGlobal('fetch', fetch);
    const { accessToken } = await import('../extension/session');
    await expect(accessToken()).rejects.toThrow('Reconnect Gmail');
    expect(local.data.connection).toBeUndefined();
    expect(session.data.token).toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps the connection during a transient service error without an automatic retry', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response(503));
    vi.stubGlobal('fetch', fetch);
    const { accessToken } = await import('../extension/session');
    await expect(accessToken()).rejects.toThrow('temporarily unavailable');
    expect(local.data.connection).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('clears local credentials and confirmations even if remote logout fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof globalThis.fetch>(async () => response(503)),
    );
    session.data.token = 'old-access';
    session.data['pending:ticket'] = { threadId: 'private' };
    const { signOut } = await import('../extension/session');
    await expect(signOut()).rejects.toThrow('server sign-out could not be confirmed');
    expect(local.data.connection).toBeUndefined();
    expect(session.data).toEqual({});
  });

  it('does not restore a credential if sign-out occurs while refresh is in flight', async () => {
    let complete: (response: Response) => void = () => undefined;
    const deferred = new Promise<Response>((resolve) => {
      complete = resolve;
    });
    const fetch = vi.fn((url: string) =>
      url.endsWith('/refresh') ? deferred : Promise.resolve(response()),
    );
    vi.stubGlobal('fetch', fetch);
    const { accessToken, signOut } = await import('../extension/session');
    const refreshing = accessToken();
    const rejection = expect(refreshing).rejects.toThrow('account changed');
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await signOut();
    complete(response());
    await rejection;
    expect(local.data.connection).toBeUndefined();
    expect(session.data.token).toBeUndefined();
  });

  it('does not leave a credential behind when sign-out races an asynchronous storage write', async () => {
    let release: () => void = () => undefined;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const writing = vi.fn();
    local.set.mockImplementationOnce(async (values) => {
      writing();
      await blocked;
      Object.assign(local.data, values);
    });
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response());
    vi.stubGlobal('fetch', fetch);
    const { accessToken, signOut } = await import('../extension/session');
    const refreshing = accessToken();
    const rejection = expect(refreshing).rejects.toThrow('account changed');
    await vi.waitFor(() => expect(writing).toHaveBeenCalled());
    const logout = signOut();
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/logout'))).toBe(true),
    );
    // Let logout finish its immediate work while Chrome's old write is pending.
    await new Promise((resolve) => setTimeout(resolve, 0));
    release();
    await Promise.all([rejection, logout]);
    expect(local.data.connection).toBeUndefined();
    expect(session.data.token).toBeUndefined();
  });

  it('does not return a private API response after the connected account changed', async () => {
    session.data.token = 'old-app-access';
    session.data.expiresAt = Date.now() + 3600000;
    let complete: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      complete = resolve;
    });
    const fetch = vi.fn((url: string) =>
      url.endsWith('/inbox/priority') ? pending : Promise.resolve(response()),
    );
    vi.stubGlobal('fetch', fetch);
    const { request, saveConnection } = await import('../extension/session');
    const oldResponse = request('/inbox/priority');
    const rejected = expect(oldResponse).rejects.toThrow('account changed');
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    await saveConnection(tokens, '22222222-2222-4222-8222-222222222222');
    complete(new Response(JSON.stringify([{ subject: 'Old account private subject' }])));
    await rejected;
  });

  it('rejects a delayed account-bound action before sending an HTTP request', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response());
    vi.stubGlobal('fetch', fetch);
    const { connectionGeneration, request, saveConnection, signOut } =
      await import('../extension/session');
    const oldVersion = connectionGeneration();
    await saveConnection(tokens, '22222222-2222-4222-8222-222222222222');
    const count = fetch.mock.calls.length;
    await expect(request('/accounts/disconnect', {}, undefined, oldVersion)).rejects.toThrow(
      'account changed',
    );
    await expect(signOut(oldVersion)).rejects.toThrow('account changed');
    expect(fetch).toHaveBeenCalledTimes(count);
    expect(local.data.connection).toBeDefined();
  });
});
