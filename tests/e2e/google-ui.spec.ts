import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';

test('Google redirect, live sync, confirmation, and Gmail link use the live interface', async ({
  page,
}) => {
  // Provider verification is covered by API tests. This isolates the browser's
  // OAuth handoff and live-account UI without contacting Google or real mail.
  let challenge = '';
  let synced = false;
  let sent = false;
  let inboxCalls = 0;
  const thread = {
    id: 'live-thread',
    subject: 'Project check-in',
    recipient_email: 'recipient@gmail.com',
    preview: '',
    last_message_at: new Date().toISOString(),
    has_reply: false,
    eligibility: 'unable_to_verify',
    provider_thread_id: '1111111111111111',
  };
  const reminder = {
    id: 'live-reminder',
    thread_id: thread.id,
    subject: thread.subject,
    preview: '',
    sender_email: 'sender@gmail.com',
    sender_name: 'Sender',
    recipient_email: 'recipient@gmail.com',
    recipient_name: 'Recipient',
    status: 'delivered',
    direction: 'sent',
    created_at: new Date().toISOString(),
    delivered_at: new Date().toISOString(),
    dismissed_at: null,
    simulated: false,
    provider_thread_id: thread.provider_thread_id,
  };
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const fulfill = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (url.pathname === '/api/health')
      return fulfill({ demo_mode: false, google_configured: true, extension_configured: true });
    if (url.pathname === '/api/auth/google/start') {
      challenge = url.searchParams.get('code_challenge') ?? '';
      expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
      expect(url.searchParams.get('redirect_uri')).toBe('http://127.0.0.1:5174/');
      return route.fulfill({
        status: 302,
        headers: {
          Location:
            'http://127.0.0.1:5174/?oauth_code=provider-handoff-code-for-ui-test-only-1234567890',
        },
      });
    }
    if (url.pathname === '/api/auth/google/exchange') {
      const body = request.postDataJSON();
      expect(createHash('sha256').update(body.verifier).digest('base64url')).toBe(challenge);
      return fulfill({ access_token: 'live-ui-test-token' });
    }
    if (request.headers().authorization !== 'Bearer live-ui-test-token')
      return fulfill({ detail: 'Authentication required' }, 401);
    if (url.pathname === '/api/me')
      return fulfill({
        id: 'real-user',
        email: 'sender@gmail.com',
        display_name: 'Sender',
        demo: false,
        provider: 'google',
        connection_status: 'connected',
        daily_used: sent ? 1 : 0,
        daily_limit: 10,
        cooldown_hours: 24,
      });
    if (url.pathname === '/api/preferences')
      return fulfill({ reminders_enabled: true, notification_enabled: true, blocked_senders: [] });
    if (url.pathname === '/api/threads') return fulfill(synced ? [thread] : []);
    if (url.pathname === '/api/threads/sync') {
      synced = true;
      return fulfill({ synced: 1, skipped: 0, next_page_token: null });
    }
    if (url.pathname === '/api/threads/live-thread/eligibility')
      return fulfill({ status: 'delivered', simulated: false });
    if (url.pathname === '/api/reminders' && request.method() === 'POST') {
      sent = true;
      expect(Object.keys(request.postDataJSON()).sort()).toEqual([
        'delivery_channel',
        'idempotency_key',
        'thread_id',
      ]);
      return fulfill(reminder, 201);
    }
    if (url.pathname === '/api/reminders') return fulfill(sent ? [reminder] : []);
    if (url.pathname === '/api/inbox/priority') return fulfill([]);
    if (url.pathname === '/api/inbox/gmail') {
      inboxCalls += 1;
      const older = url.searchParams.has('page_token');
      return fulfill({
        threads: [
          {
            provider_thread_id: older ? '2222222222222222' : '3333333333333333',
            sender_name: older ? 'Jordan Lee' : 'Maya Chen',
            sender_email: older ? 'jordan@gmail.com' : 'maya@gmail.com',
            subject: older ? 'Older inbox conversation' : 'An actual inbox conversation',
            last_message_at: new Date().toISOString(),
            unread: !older,
            message_count: 1,
          },
        ],
        next_page_token: older ? null : 'older-inbox-page',
      });
    }
    throw new Error(`Unexpected browser API request: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Connect Google', exact: true }).click();
  await expect(page.getByText('Google account connected', { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.has('oauth_code')).toBe(false);
  expect(await page.evaluate(() => sessionStorage.getItem('nudge-oauth-verifier'))).toBeNull();
  await expect(page.getByText('Demo workspace', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Your Gmail inbox.' })).toBeVisible();
  await expect(page.getByText('An actual inbox conversation', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Open An actual inbox conversation in Gmail' }),
  ).toHaveAttribute('href', /authuser=sender%40gmail.com#all\/3333333333333333$/);
  await page.getByRole('button', { name: 'Load older conversations' }).click();
  await expect(page.getByText('Older inbox conversation', { exact: true })).toBeVisible();
  await expect(page.getByText('An actual inbox conversation', { exact: true })).toBeVisible();
  expect(inboxCalls).toBe(2);
  await page
    .getByRole('textbox', { name: 'Search loaded Gmail conversations' })
    .fill('Older inbox');
  await expect(page.getByText('Older inbox conversation', { exact: true })).toBeVisible();
  await expect(page.getByText('An actual inbox conversation', { exact: true })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Search loaded Gmail conversations' }).clear();
  await page.screenshot({ path: 'test-results/previews/gmail-inbox.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(
    page.locator('.mobile-nav').getByRole('button', { name: 'Gmail inbox' }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole('button', { name: 'Send a nudge', exact: true }).click();
  await page.getByRole('button', { name: 'Sync recent' }).click();
  await page.getByRole('button', { name: 'Remind recipient', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('recipient@gmail.com', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Send reminder', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: 'Reminder delivered' })).toBeVisible();
  await expect(dialog.getByText('Demo only. No live recipient is contacted.')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Done' }).click();
  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name: 'Reminder history', exact: true })
    .click();
  await page.locator('.history-row').click();
  const link = dialog.getByRole('link', { name: 'Open original in Gmail' });
  await expect(link).toHaveAttribute('href', /authuser=sender%40gmail.com#all\/1111111111111111$/);
});
