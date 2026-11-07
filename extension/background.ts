import type { DeliveryPlan, Preferences, Reminder, User } from '../src/types';
import { API_ORIGIN } from '../src/config';
import { authStartUrl, createPkce } from '../src/oauth';
import {
  assertConnection,
  connectionGeneration,
  ConnectionRequired,
  installationId,
  request,
  saveConnection,
  signOut,
} from './session';

const allowedOrigin = 'https://mail.google.com';
let polling = false;
let connecting = false;
const setup = () => chrome.tabs.create({ url: chrome.runtime.getURL('index.html') });
const notificationsAllowed = () =>
  new Promise<boolean>((resolve) =>
    chrome.notifications.getPermissionLevel((level) => resolve(level === 'granted')),
  );

async function updateGmailTabs() {
  for (const tab of await chrome.tabs.query({})) {
    if (typeof tab.id === 'number')
      void chrome.tabs.sendMessage(tab.id, { type: 'CONNECTION_CHANGED' }).catch(() => undefined);
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  const version = connectionGeneration();
  try {
    const user = await request<User>('/me');
    const prefs = await request<Preferences>('/preferences');
    const id = await installationId();
    assertConnection(version);
    if (!id || user.demo) return;
    const permitted = await notificationsAllowed();
    await request('/extension/installation', {
      installation_id: id,
      notifications_enabled: permitted && prefs.notification_enabled && prefs.reminders_enabled,
    });
    const reminders = await request<Reminder[]>('/inbox/priority');
    assertConnection(version);
    await chrome.action.setBadgeText({ text: reminders.length ? String(reminders.length) : '' });
    await chrome.action.setBadgeBackgroundColor({ color: '#507449' });
    if (!permitted || !prefs.notification_enabled || !prefs.reminders_enabled) return;
    const seen: string[] = (await chrome.storage.local.get('seenReminders')).seenReminders ?? [];
    for (const reminder of reminders.slice(0, 20)) {
      if (
        reminder.direction !== 'received' ||
        reminder.simulated ||
        seen.includes(reminder.id) ||
        reminder.notification_status === 'extension_notified' ||
        Date.parse(reminder.created_at) < Date.now() - 7 * 86400000
      )
        continue;
      // Recheck account binding before a device-visible action.
      if ((await installationId()) !== id) return;
      assertConnection(version);
      await chrome.notifications.create(`nudge:${user.id}:${reminder.id}`, {
        type: 'basic',
        iconUrl: chrome.runtime.getURL('notification-icon.png'),
        title: 'Inbox Nudge',
        message: 'Someone nudged an email conversation. Click to open it in Gmail.',
        priority: 0,
      });
      seen.push(reminder.id);
      await chrome.storage.local.set({ seenReminders: seen.slice(-500) });
      await request(`/extension/reminders/${encodeURIComponent(reminder.id)}/notified`, {}).catch(
        () => undefined,
      );
    }
  } catch {
    await chrome.action.setBadgeText({ text: '' });
  } finally {
    polling = false;
  }
}

chrome.runtime.onInstalled.addListener((details) => {
  chrome.alarms.create('nudge-poll', { periodInMinutes: 1 });
  if (details.reason === 'install') void setup();
  void poll();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('nudge-poll', { periodInMinutes: 1 });
  void poll();
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'nudge-poll') void poll();
});
chrome.notifications.onPermissionLevelChanged.addListener(() => void poll());
chrome.notifications.onClicked.addListener((id) => {
  void (async () => {
    const parts = id.split(':');
    if (parts.length !== 3 || parts[0] !== 'nudge' || !/^[a-zA-Z0-9-]{1,40}$/.test(parts[2]))
      return;
    try {
      const user = await request<User>('/me');
      if (user.id !== parts[1]) return;
      const reminder = await request<Reminder>(`/reminders/${parts[2]}`);
      if (reminder.direction !== 'received') return;
      const thread = reminder.provider_thread_id;
      const url = `https://mail.google.com/mail/u/?authuser=${encodeURIComponent(user.email)}${thread && /^[a-fA-F0-9]{10,32}$/.test(thread) ? '#all/' + thread : '#inbox'}`;
      await chrome.tabs.create({ url });
      await chrome.notifications.clear(id);
    } catch {
      await setup();
    }
  })();
});

async function connect(emailReminders: boolean) {
  if (connecting) throw new Error('Gmail connection is already in progress.');
  connecting = true;
  try {
    const pkce = await createPkce();
    const redirect = chrome.identity.getRedirectURL('google');
    const final = await chrome.identity.launchWebAuthFlow({
      url: authStartUrl(redirect, pkce.challenge, emailReminders),
      interactive: true,
    });
    if (!final) throw new Error('Google sign-in was cancelled.');
    const destination = new URL(final);
    if (destination.origin + destination.pathname !== redirect)
      throw new Error('Unexpected sign-in destination.');
    if (destination.searchParams.has('oauth_error'))
      throw new Error(destination.searchParams.get('oauth_error')!);
    const code = destination.searchParams.get('oauth_code');
    if (!code) throw new Error('Google did not return a sign-in code.');
    const id = crypto.randomUUID();
    const result = await fetch(`${API_ORIGIN}/api/auth/google/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, verifier: pkce.verifier, installation_id: id }),
      signal: AbortSignal.timeout(15000),
    });
    if (!result.ok)
      throw new Error('Unable to finish sign-in. Check the extension configuration and reconnect.');
    await saveConnection(await result.json(), id);
    await poll();
    await updateGmailTabs();
    return { ok: true };
  } finally {
    connecting = false;
  }
}

async function trustedAction(message: Record<string, unknown>) {
  const version = connectionGeneration();
  if (
    [
      'SAVE_EXTENSION_PREFERENCES',
      'SIGN_OUT_EXTENSION',
      'DISCONNECT_EXTENSION',
      'CHECK_EMAIL_STATUS',
    ].includes(String(message.type))
  ) {
    const user = await request<User>('/me', undefined, undefined, version);
    if (message.accountId !== user.id)
      throw new Error(
        'The connected account changed. Reopen the extension before changing settings.',
      );
  }
  if (message.type === 'CONNECT_GOOGLE') return connect(message.emailReminders !== false);
  if (message.type === 'EXTENSION_STATE') {
    const response = await fetch(`${API_ORIGIN}/api/health`, {
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Inbox Nudge is temporarily unavailable.');
    const health = await response.json();
    try {
      const [user, preferences, history] = await Promise.all([
        request<User>('/me'),
        request<Preferences>('/preferences'),
        request<Reminder[]>('/reminders?limit=5'),
      ]);
      void poll();
      return {
        ok: true,
        health,
        user,
        preferences,
        history,
        alertsAllowed: await notificationsAllowed(),
      };
    } catch (error) {
      if (!(error instanceof ConnectionRequired)) throw error;
      return { ok: true, health, user: null };
    }
  }
  if (message.type === 'SAVE_EXTENSION_PREFERENCES') {
    // Only a fixed payload is accepted, even from extension pages.
    const input = message.preferences as Preferences | undefined;
    if (
      !input ||
      typeof input.reminders_enabled !== 'boolean' ||
      typeof input.notification_enabled !== 'boolean' ||
      !Array.isArray(input.blocked_senders) ||
      input.blocked_senders.some((value) => typeof value !== 'string')
    )
      throw new Error('Invalid preferences.');
    const preferences = await request<Preferences>(
      '/preferences',
      {
        reminders_enabled: input.reminders_enabled,
        notification_enabled: input.notification_enabled,
        blocked_senders: input.blocked_senders,
      },
      'PUT',
      version,
    );
    await poll();
    return { ok: true, preferences };
  }
  if (message.type === 'SIGN_OUT_EXTENSION' || message.type === 'DISCONNECT_EXTENSION') {
    let googleRevoked = true;
    if (message.type === 'DISCONNECT_EXTENSION')
      googleRevoked = (
        await request<{ google_revoked: boolean }>('/accounts/disconnect', {}, undefined, version)
      ).google_revoked;
    try {
      await signOut(version);
    } finally {
      await chrome.action.setBadgeText({ text: '' });
      await updateGmailTabs();
    }
    return { ok: true, googleRevoked };
  }
  if (message.type === 'OPEN_GMAIL') {
    await chrome.tabs.create({ url: 'https://mail.google.com/mail/u/#sent' });
    return { ok: true };
  }
  if (
    message.type === 'CHECK_EMAIL_STATUS' &&
    typeof message.id === 'string' &&
    /^[a-zA-Z0-9-]{1,40}$/.test(message.id)
  )
    return {
      ok: true,
      reminder: await request<Reminder>(
        `/reminders/${message.id}/reconcile`,
        {},
        undefined,
        version,
      ),
    };
  throw new Error('Unsupported extension action.');
}

type Pending = {
  threadId: string;
  userId: string;
  mailbox: string;
  tabId: number;
  expires: number;
  key: string;
  channel: 'in_app' | 'email';
  reminderId?: string;
};

async function gmailAction(message: Record<string, unknown>, sender: chrome.runtime.MessageSender) {
  if (message.type === 'OPEN_EXTENSION' || message.type === 'OPEN_WORKSPACE') {
    await setup();
    return { ok: true };
  }
  if (message.type === 'GMAIL_CONTEXT') {
    const user = await request<User>('/me');
    if (
      user.demo ||
      typeof message.mailbox !== 'string' ||
      user.email.toLowerCase() !== message.mailbox.toLowerCase()
    )
      return { ok: false };
    const [preferences, reminders] = await Promise.all([
      request<Preferences>('/preferences'),
      request<Reminder[]>('/inbox/priority'),
    ]);
    return { ok: true, email: user.email, demo: false, preferences, reminders };
  }
  if (message.type === 'DISMISS_GMAIL_REMINDER') {
    const user = await request<User>('/me');
    if (
      message.mailbox !== user.email ||
      typeof message.id !== 'string' ||
      !/^[a-zA-Z0-9-]{1,40}$/.test(message.id)
    )
      throw new Error('This Gmail account does not match your extension connection.');
    await request(`/reminders/${message.id}/dismiss`, {});
    void poll();
    return { ok: true };
  }
  if (message.type === 'PREPARE_REMINDER') {
    const user = await request<User>('/me');
    if (
      typeof message.mailbox !== 'string' ||
      message.mailbox.toLowerCase() !== user.email.toLowerCase() ||
      user.demo
    )
      throw new Error('Connect this Gmail account using the Inbox Nudge extension icon.');
    const reference = message.reference as { id?: unknown; kind?: unknown } | undefined;
    const validReference =
      reference &&
      typeof reference.id === 'string' &&
      /^[a-fA-F0-9]{10,32}$/.test(reference.id) &&
      ['thread', 'message'].includes(String(reference.kind));
    if (
      !validReference &&
      (typeof message.subject !== 'string' ||
        message.subject.length > 500 ||
        typeof message.recipient !== 'string')
    )
      throw new Error('Reload Gmail and reopen this sent conversation so it can be verified.');
    const thread = validReference
      ? await request<{ id: string; subject: string; recipient_email: string }>('/threads/import', {
          [reference!.kind === 'thread' ? 'provider_thread_id' : 'provider_message_id']:
            reference!.id,
        })
      : await request<{ id: string; subject: string; recipient_email: string }>(
          '/threads/resolve',
          { subject: message.subject, recipient_email: message.recipient },
        );
    const eligibility = await request<DeliveryPlan>(`/threads/${thread.id}/eligibility`);
    const ticket = crypto.randomUUID();
    // Expired confirmations are discarded, rather than growing session storage.
    const saved = await chrome.storage.session.get(null);
    const expired = Object.keys(saved).filter(
      (key) => key.startsWith('pending:') && saved[key].expires < Date.now(),
    );
    if (expired.length) await chrome.storage.session.remove(expired);
    const pending: Pending = {
      threadId: thread.id,
      userId: user.id,
      mailbox: user.email,
      tabId: sender.tab!.id!,
      expires: Date.now() + 300000,
      key: crypto.randomUUID(),
      channel: eligibility.delivery_channel,
    };
    await chrome.storage.session.set({ [`pending:${ticket}`]: pending });
    return {
      ok: true,
      thread,
      status: eligibility.status,
      email_preview: eligibility.email_preview,
      ticket,
    };
  }
  if (message.type === 'SUBMIT_REMINDER' || message.type === 'REMINDER_STATUS') {
    if (typeof message.ticket !== 'string' || !/^[a-f0-9-]{36}$/.test(message.ticket))
      throw new Error('Invalid reminder confirmation.');
    const name = `pending:${message.ticket}`;
    const pending: Pending | undefined = (await chrome.storage.session.get(name))[name];
    const user = await request<User>('/me');
    if (
      !pending ||
      pending.expires < Date.now() ||
      pending.tabId !== sender.tab?.id ||
      pending.userId !== user.id ||
      pending.mailbox !== user.email
    )
      throw new Error('The confirmation expired or the account changed. Click Nudge again.');
    if (message.type === 'REMINDER_STATUS') {
      if (!pending.reminderId) throw new Error('No reminder was submitted.');
      return {
        ok: true,
        reminder: await request<Reminder>(`/reminders/${encodeURIComponent(pending.reminderId)}`),
      };
    }
    const reminder = await request<Reminder>('/reminders', {
      thread_id: pending.threadId,
      idempotency_key: pending.key,
      delivery_channel: pending.channel,
    });
    await chrome.storage.session.set({ [name]: { ...pending, reminderId: reminder.id } });
    return { ok: true, reminder };
  }
  throw new Error('Unsupported Gmail action.');
}

chrome.runtime.onMessage.addListener((message: unknown, sender, respond) => {
  if (
    sender.id !== chrome.runtime.id ||
    !sender.url ||
    !message ||
    typeof message !== 'object' ||
    !('type' in message)
  )
    return;
  const input = message as Record<string, unknown>;
  const extensionPage = sender.url.startsWith(chrome.runtime.getURL(''));
  const gmailPage =
    new URL(sender.url).origin === allowedOrigin &&
    sender.frameId === 0 &&
    typeof sender.tab?.id === 'number';
  if (!extensionPage && !gmailPage) return;
  const version = connectionGeneration();
  (extensionPage ? trustedAction(input) : gmailAction(input, sender))
    .then((result) => {
      if (!extensionPage) assertConnection(version);
      return result;
    })
    .then(respond)
    .catch((error: Error) => respond({ ok: false, error: error.message }));
  return true;
});
