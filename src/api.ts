import type { DeliveryPlan, GmailInboxPage, Preferences, Reminder, Thread, User } from './types';
import { API_ORIGIN } from './config';
import { authStartUrl, createPkce } from './oauth';

const isExtension = typeof chrome !== 'undefined' && Boolean(chrome.runtime?.id);
let browserToken: string | null = sessionStorage.getItem('nudge-token');

async function token() {
  if (isExtension) return (await chrome.storage.session.get('token')).token as string | undefined;
  return browserToken;
}

export async function setToken(value: string | null) {
  if (isExtension) {
    if (value) await chrome.storage.session.set({ token: value });
    else await chrome.storage.session.remove('token');
  } else {
    browserToken = value;
    if (value) sessionStorage.setItem('nudge-token', value);
    else sessionStorage.removeItem('nudge-token');
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await token();
  const response = await fetch(
    `${isExtension || !import.meta.env.DEV ? API_ORIGIN : ''}/api${path}`,
    {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        ...init.headers,
      },
      signal: init.signal ?? AbortSignal.timeout(60000),
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      typeof body.detail === 'string'
        ? body.detail
        : 'Unable to complete the request. Please try again.',
    );
  }
  return response.json() as Promise<T>;
}

export const api = {
  async connectGoogle(emailReminders = false) {
    if (isExtension) {
      const result = await chrome.runtime.sendMessage({ type: 'CONNECT_GOOGLE', emailReminders });
      if (!result?.ok) throw new Error(result?.error || 'Google connection was cancelled.');
      return;
    }
    const pkce = await createPkce();
    sessionStorage.setItem('nudge-oauth-verifier', pkce.verifier);
    if (new URLSearchParams(location.search).get('view') === 'priority')
      sessionStorage.setItem('nudge-view', 'priority');
    location.assign(authStartUrl(`${location.origin}/`, pkce.challenge, emailReminders));
  },
  async completeGoogleRedirect() {
    const params = new URLSearchParams(location.search);
    const code = params.get('oauth_code'),
      error = params.get('oauth_error');
    if (!code && !error) return;
    window.history.replaceState(null, '', location.pathname);
    const verifier = sessionStorage.getItem('nudge-oauth-verifier');
    sessionStorage.removeItem('nudge-oauth-verifier');
    if (error) throw new Error(error);
    if (!verifier)
      throw new Error('This sign-in was not started in this tab. Please connect again.');
    const result = await request<{ access_token: string }>('/auth/google/exchange', {
      method: 'POST',
      body: JSON.stringify({ code, verifier }),
    });
    await setToken(result.access_token);
  },
  disconnect: () =>
    request<{ disconnected: boolean; google_revoked: boolean }>('/accounts/disconnect', {
      method: 'POST',
    }),
  sync: (pageToken?: string) =>
    request<{ synced: number; skipped: number; next_page_token: string | null }>('/threads/sync', {
      method: 'POST',
      body: JSON.stringify({ page_token: pageToken ?? null }),
    }),
  async login(persona: 'alex' | 'maya') {
    const result = await request<{ access_token: string }>('/auth/demo', {
      method: 'POST',
      body: JSON.stringify({ persona }),
    });
    await setToken(result.access_token);
  },
  me: () => request<User>('/me'),
  priority: () => request<Reminder[]>('/inbox/priority'),
  gmailInbox: (pageToken: string | undefined, signal: AbortSignal) => {
    const query = pageToken ? `?page_token=${encodeURIComponent(pageToken)}` : '';
    return request<GmailInboxPage>(`/inbox/gmail${query}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(60000)]),
    });
  },
  history: () => request<Reminder[]>('/reminders?limit=100'),
  threads: () => request<Thread[]>('/threads'),
  preferences: () => request<Preferences>('/preferences'),
  savePreferences: (value: Preferences) =>
    request<Preferences>('/preferences', { method: 'PUT', body: JSON.stringify(value) }),
  eligibility: (id: string) =>
    request<DeliveryPlan>(`/threads/${encodeURIComponent(id)}/eligibility`),
  remind: (id: string, key: string, channel: 'in_app' | 'email' = 'in_app') =>
    request<Reminder>('/reminders', {
      method: 'POST',
      body: JSON.stringify({ thread_id: id, idempotency_key: key, delivery_channel: channel }),
    }),
  reminder: (id: string) => request<Reminder>(`/reminders/${encodeURIComponent(id)}`),
  reconcile: (id: string) =>
    request<Reminder>(`/reminders/${encodeURIComponent(id)}/reconcile`, { method: 'POST' }),
  dismiss: (id: string) =>
    request<Reminder>(`/reminders/${encodeURIComponent(id)}/dismiss`, { method: 'POST' }),
};

export function openWorkspace() {
  if (isExtension) chrome.tabs.create({ url: chrome.runtime.getURL('index.html') });
}

export { isExtension };
