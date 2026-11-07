import { API_ORIGIN } from '../src/config';

const BASE = `${API_ORIGIN}/api`;
// Local storage is not exposed to Gmail content scripts. Google tokens stay on the server.
const storageReady = Promise.all([
  chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' }),
  chrome.storage.session.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' }),
]);
let refreshing: Promise<string> | null = null;
let generation = 0;
let pendingWrite: Promise<unknown> = Promise.resolve();
export class ConnectionRequired extends Error {}

function writeCredentials<T>(work: () => Promise<T>): Promise<T> {
  const result = pendingWrite.then(work);
  pendingWrite = result.catch(() => undefined);
  return result;
}

export const connectionGeneration = () => generation;
export function assertConnection(version: number) {
  if (version !== generation) throw new Error('The connected account changed. Please try again.');
}

interface Tokens {
  access_token: string;
  expires_in: number;
  refresh_token: string;
}
interface Connection {
  refreshToken: string;
  installationId: string;
}

async function connection(): Promise<Connection | undefined> {
  await storageReady;
  return (await chrome.storage.local.get('connection')).connection;
}

export async function saveConnection(tokens: Tokens, installationId: string) {
  await storageReady;
  if (
    typeof tokens.access_token !== 'string' ||
    typeof tokens.refresh_token !== 'string' ||
    !Number.isFinite(tokens.expires_in)
  )
    throw new Error('Unable to save this connection. Please reconnect Gmail.');
  const version = ++generation;
  const previous = await connection();
  if (previous) await revoke(previous).catch(() => undefined);
  await writeCredentials(async () => {
    assertConnection(version);
    await chrome.storage.session.clear();
    await chrome.storage.local.set({
      connection: { refreshToken: tokens.refresh_token, installationId },
      seenReminders: [],
    });
    assertConnection(version);
    await chrome.storage.session.set({
      token: tokens.access_token,
      expiresAt: Date.now() + tokens.expires_in * 1000,
    });
    assertConnection(version);
  });
}

async function revoke(value: Connection) {
  const result = await fetch(`${BASE}/auth/extension/logout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      refresh_token: value.refreshToken,
      installation_id: value.installationId,
    }),
    signal: AbortSignal.timeout(10000),
  });
  if (!result.ok)
    throw new Error('Local sign-out completed, but server sign-out could not be confirmed.');
}

export async function signOut(expectedVersion?: number) {
  await storageReady;
  if (expectedVersion !== undefined) assertConnection(expectedVersion);
  generation++;
  const previous = await connection();
  try {
    if (previous) await revoke(previous);
  } finally {
    await writeCredentials(async () => {
      await chrome.storage.session.clear();
      await chrome.storage.local.remove(['connection', 'seenReminders']);
    });
  }
}

export async function installationId() {
  return (await connection())?.installationId;
}

export async function accessToken(): Promise<string> {
  await storageReady;
  const current = await chrome.storage.session.get(['token', 'expiresAt']);
  if (typeof current.token === 'string' && current.expiresAt > Date.now() + 60000)
    return current.token;
  if (refreshing) return refreshing;
  const version = generation;
  refreshing = (async () => {
    const saved = await connection();
    if (!saved) throw new ConnectionRequired('Connect Gmail using the Inbox Nudge extension.');
    const result = await fetch(`${BASE}/auth/extension/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        refresh_token: saved.refreshToken,
        installation_id: saved.installationId,
      }),
      signal: AbortSignal.timeout(15000),
    });
    if (version !== generation) throw new Error('The connected account changed. Please try again.');
    if (!result.ok) {
      if (result.status === 401) {
        await writeCredentials(async () => {
          assertConnection(version);
          await chrome.storage.session.clear();
          await chrome.storage.local.remove('connection');
        });
      }
      const Failure = result.status === 401 ? ConnectionRequired : Error;
      throw new Failure(
        result.status === 401
          ? 'Reconnect Gmail using the extension.'
          : 'Inbox Nudge is temporarily unavailable.',
      );
    }
    const tokens: Tokens = await result.json();
    if (
      typeof tokens.access_token !== 'string' ||
      typeof tokens.refresh_token !== 'string' ||
      !Number.isFinite(tokens.expires_in)
    )
      throw new Error('Unable to renew this connection. Reconnect Gmail.');
    if (version !== generation) throw new Error('The connected account changed.');
    // Store the rotated credential before exposing its short-lived access token.
    await writeCredentials(async () => {
      assertConnection(version);
      await chrome.storage.local.set({
        connection: { ...saved, refreshToken: tokens.refresh_token },
      });
      assertConnection(version);
      await chrome.storage.session.set({
        token: tokens.access_token,
        expiresAt: Date.now() + tokens.expires_in * 1000,
      });
      assertConnection(version);
    });
    return tokens.access_token;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

export async function request<T>(
  path: string,
  body?: unknown,
  method?: 'PUT',
  version = generation,
): Promise<T> {
  assertConnection(version);
  const token = await accessToken();
  assertConnection(version);
  const result = await fetch(`${BASE}${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(60000),
  });
  assertConnection(version);
  if (!result.ok) {
    if (result.status === 401 && version === generation) {
      await writeCredentials(async () => {
        assertConnection(version);
        generation++;
        await chrome.storage.session.clear();
        await chrome.storage.local.remove('connection');
      });
    }
    const body = await result.json().catch(() => ({}));
    const Failure = result.status === 401 ? ConnectionRequired : Error;
    throw new Failure(
      result.status === 401
        ? 'Reconnect Gmail using the extension.'
        : typeof body.detail === 'string'
          ? body.detail
          : 'Unable to reach Inbox Nudge. Try again.',
    );
  }
  const value = (await result.json()) as T;
  assertConnection(version);
  return value;
}
