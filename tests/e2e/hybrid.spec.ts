import { test, expect, type Page } from '@playwright/test';

async function liveApp(page: Page, grant = true) {
  const state = { submissions: 0, polls: 0, emailConsent: false, subscriptions: 0 };
  const thread = {
    id: 'email-thread',
    subject: 'Project check-in',
    recipient_email: 'recipient@gmail.com',
    preview: '',
    last_message_at: new Date().toISOString(),
    has_reply: false,
    eligibility: grant ? 'email_ready' : 'email_permission_required',
    provider_thread_id: '1111111111111111',
  };
  const reminder = {
    id: 'email-reminder',
    thread_id: thread.id,
    subject: thread.subject,
    preview: '',
    sender_email: 'sender@gmail.com',
    sender_name: 'Sender',
    recipient_email: thread.recipient_email,
    recipient_name: 'Recipient',
    status: 'email_pending',
    direction: 'sent',
    created_at: new Date().toISOString(),
    delivered_at: null,
    dismissed_at: null,
    simulated: false,
    provider_thread_id: thread.provider_thread_id,
    delivery_channel: 'email',
    notification_status: 'none',
  };
  await page.addInitScript(() => sessionStorage.setItem('nudge-token', 'hybrid-test-token'));
  await page.route('**/api/**', async (route) => {
    const req = route.request(),
      url = new URL(req.url());
    const fulfill = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (url.pathname === '/api/health')
      return fulfill({ demo_mode: false, google_configured: true });
    if (url.pathname === '/api/me')
      return fulfill({
        id: 'sender',
        email: 'sender@gmail.com',
        display_name: 'Sender',
        demo: false,
        provider: 'google',
        connection_status: 'connected',
        daily_used: state.submissions,
        daily_limit: 10,
        cooldown_hours: 24,
        gmail_send_enabled: grant,
      });
    if (url.pathname === '/api/preferences')
      return fulfill({ reminders_enabled: true, notification_enabled: true, blocked_senders: [] });
    if (url.pathname === '/api/threads') return fulfill([thread]);
    if (url.pathname === '/api/inbox/priority') return fulfill([]);
    if (url.pathname === '/api/inbox/gmail') return fulfill({ threads: [], next_page_token: null });
    if (url.pathname.endsWith('/eligibility'))
      return fulfill({
        status: thread.eligibility,
        simulated: false,
        delivery_channel: 'email',
        push_available: false,
        email_preview:
          'Hi,\n\nJust a gentle reminder about my earlier email.\n\nThanks,\nSender\n\nSent with Inbox Nudge.',
      });
    if (url.pathname === '/api/reminders' && req.method() === 'POST') {
      expect(req.postDataJSON().delivery_channel).toBe('email');
      expect(req.postDataJSON().thread_id).toBe(thread.id);
      state.submissions++;
      return fulfill(reminder, 201);
    }
    if (url.pathname === '/api/reminders') return fulfill(state.submissions ? [reminder] : []);
    if (url.pathname === '/api/reminders/email-reminder') {
      state.polls++;
      return fulfill({ ...reminder, status: 'email_sent', delivered_at: new Date().toISOString() });
    }
    if (url.pathname === '/api/auth/google/start') {
      state.emailConsent = url.searchParams.get('email_reminders') === 'true';
      return route.fulfill({ status: 200, contentType: 'text/plain', body: 'Mock Google consent' });
    }
    if (url.pathname === '/api/notifications/config')
      return fulfill({
        configured: true,
        public_key: 'BMockPublicKey',
        subscriptions: state.subscriptions,
        web_app_origin: 'http://127.0.0.1:5174',
      });
    if (url.pathname === '/api/notifications/subscriptions') {
      expect(req.postDataJSON().endpoint).toBe('https://fcm.googleapis.com/fcm/send/browser-mock');
      state.subscriptions++;
      return fulfill({ id: 'mock-sub', active: true });
    }
    throw new Error(`Unexpected hybrid UI request: ${req.method()} ${url.pathname}`);
  });
  await page.goto('/');
  return state;
}

test('email preview can be cancelled, then explicitly submitted and polled to acceptance', async ({
  page,
}) => {
  const state = await liveApp(page);
  await page.getByRole('button', { name: 'Send a nudge', exact: true }).click();
  await page.getByRole('button', { name: 'Remind recipient', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('.email-preview')).toContainText('Just a gentle reminder');
  await expect(dialog.locator('.email-preview')).toContainText('This creates a new email');
  await dialog.getByRole('button', { name: 'Maybe later' }).click();
  expect(state.submissions).toBe(0);
  await page.getByRole('button', { name: 'Remind recipient', exact: true }).click();
  await dialog.getByRole('button', { name: 'Send email reminder', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: 'Email reminder queued' })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: 'Email reminder sent' })).toBeVisible();
  await expect(dialog).toContainText('Gmail accepted the reminder email');
  expect(state.submissions).toBe(1);
  expect(state.polls).toBeGreaterThan(0);
  await page.screenshot({ path: 'test-results/previews/email-reminder.png', fullPage: true });
});

test('missing send permission opens an explicit Google consent request without sending', async ({
  page,
}) => {
  const state = await liveApp(page, false);
  await page.getByRole('button', { name: 'Send a nudge', exact: true }).click();
  await page.getByRole('button', { name: 'Remind recipient', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Enable email reminders' }).click();
  await expect(page.getByText('Mock Google consent')).toBeVisible();
  expect(state.emailConsent).toBe(true);
  expect(state.submissions).toBe(0);
});

test('email opt-out is available without login and requires a deliberate click', async ({
  page,
}) => {
  let blocked = false;
  await page.route('**/api/**', (route) => {
    const req = route.request();
    expect(new URL(req.url()).pathname).toMatch(/^\/api\/email\/preferences\/mock-capability/);
    if (req.method() === 'POST') blocked = true;
    return route.fulfill({ json: { sender_name: 'Sender', blocked } });
  });
  await page.goto('/?email_preferences=mock-capability');
  await expect(page.getByText('Reminders from Sender')).toBeVisible();
  expect(blocked).toBe(false);
  await page.getByRole('button', { name: 'Stop email reminders' }).click();
  await expect(page.getByRole('status')).toContainText(
    'Email reminders from this sender are stopped.',
  );
  expect(blocked).toBe(true);
});

test('browser notification permission is requested only after the enable click', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const fake = {
      endpoint: 'https://fcm.googleapis.com/fcm/send/browser-mock',
      toJSON: () => ({
        endpoint: 'https://fcm.googleapis.com/fcm/send/browser-mock',
        keys: { p256dh: 'mock', auth: 'mock' },
      }),
    };
    const registration = {
      pushManager: { getSubscription: async () => null, subscribe: async () => fake },
    };
    Object.defineProperty(window, 'PushManager', { value: class {} });
    Object.defineProperty(window, 'Notification', {
      value: {
        requestPermission: async () => {
          sessionStorage.setItem('permission-asked', 'yes');
          return 'granted';
        },
      },
    });
    Object.defineProperty(navigator, 'serviceWorker', {
      value: {
        getRegistration: async () => undefined,
        register: async () => registration,
        ready: Promise.resolve(registration),
      },
    });
  });
  const state = await liveApp(page);
  await page
    .locator('.sidebar-bottom')
    .getByRole('button', { name: 'Preferences', exact: true })
    .click();
  const enable = page.getByRole('button', { name: 'Enable notifications', exact: true });
  await expect(enable).toBeEnabled();
  expect(await page.evaluate(() => sessionStorage.getItem('permission-asked'))).toBeNull();
  await enable.click();
  await expect(
    page.getByText('This browser is registered. Your reminder preferences still apply.'),
  ).toBeVisible();
  expect(state.subscriptions).toBe(1);
  expect(await page.evaluate(() => sessionStorage.getItem('permission-asked'))).toBe('yes');
});
