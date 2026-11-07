import { test, expect, chromium, type CDPSession, type Page } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

interface Node {
  nodeId: number;
  nodeName: string;
  nodeValue: string;
  attributes?: string[];
  children?: Node[];
  shadowRoots?: Node[];
}
interface MockState {
  posts: Record<string, unknown>[];
  notifications: { message: string }[];
  refreshes: number;
  consent: boolean;
  mode: string;
  incoming: boolean;
  accepted: boolean;
}
function nodes(node: Node): Node[] {
  return [
    node,
    ...(node.children ?? []).flatMap(nodes),
    ...(node.shadowRoots ?? []).flatMap(nodes),
  ];
}
async function shadow(cdp: CDPSession) {
  const { root } = await cdp.send('DOM.getDocument', { depth: -1, pierce: true });
  const host = nodes(root).find((node) => node.attributes?.includes('inbox-nudge-root'));
  return host ? (host.shadowRoots ?? []).flatMap(nodes) : [];
}
async function clickShadow(cdp: CDPSession, label: string) {
  const button = (await shadow(cdp)).find(
    (node) =>
      node.nodeName === 'BUTTON' && node.children?.some((child) => child.nodeValue === label),
  );
  expect(button, `Gmail extension button ${label}`).toBeDefined();
  const { object } = await cdp.send('DOM.resolveNode', { nodeId: button!.nodeId });
  await cdp.send('Runtime.callFunctionOn', {
    objectId: object.objectId!,
    functionDeclaration: 'function(){ this.click(); }',
  });
}
async function shadowText(cdp: CDPSession) {
  return (await shadow(cdp)).map((node) => node.nodeValue).join(' ');
}
async function gmailContext(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  const contexts: { id: number; name: string }[] = [];
  cdp.on('Runtime.executionContextCreated', (event) => contexts.push(event.context));
  await cdp.send('Runtime.enable');
  await expect.poll(() => contexts.some((value) => value.name === 'Inbox Nudge')).toBe(true);
  return { cdp, contextId: contexts.find((value) => value.name === 'Inbox Nudge')!.id };
}

test('extension setup, Gmail nudge, secure bridge, renewal and Chrome alerts need no website', async () => {
  test.setTimeout(60000);
  const profile = await mkdtemp(join(tmpdir(), 'inbox-nudge-chrome-'));
  const extensionPath = resolve('dist/e2e-extension');
  const context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium',
    headless: true,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
  });
  try {
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
    const extensionId = new URL(worker.url()).host;
    await worker.evaluate(() => {
      const state: MockState = {
        posts: [],
        notifications: [],
        refreshes: 0,
        consent: false,
        mode: 'in_app',
        incoming: false,
        accepted: false,
      };
      Object.assign(globalThis, { nudgeTestState: state });
      Object.defineProperty(chrome.identity, 'launchWebAuthFlow', {
        value: async (options: { url: string }) => {
          state.consent = new URL(options.url).searchParams.get('email_reminders') === 'true';
          return (
            chrome.identity.getRedirectURL('google') +
            '?oauth_code=' +
            'mock-provider-code'.repeat(4)
          );
        },
      });
      Object.defineProperty(chrome.notifications, 'create', {
        value: async (_id: string, options: { message: string }) => {
          state.notifications.push(options);
          return _id;
        },
      });
      Object.defineProperty(chrome.notifications, 'getPermissionLevel', {
        value: (callback: (permission: string) => void) => callback('granted'),
      });
      const user = {
        id: 'real-user',
        email: 'sender@gmail.com',
        display_name: 'Sender',
        demo: false,
        provider: 'google',
        connection_status: 'connected',
        daily_used: 0,
        daily_limit: 10,
        cooldown_hours: 24,
        gmail_send_enabled: true,
      };
      const reminder = {
        id: 'reminder-123',
        thread_id: 'thread-123',
        subject: 'Project check-in',
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
        provider_thread_id: '1111111111111111',
        delivery_channel: 'in_app',
        notification_status: 'none',
      };
      globalThis.fetch = async (input, init) => {
        const path = new URL(String(input)).pathname;
        const json = (value: unknown, status = 200) =>
          Promise.resolve(
            new Response(JSON.stringify(value), {
              status,
              headers: { 'Content-Type': 'application/json' },
            }),
          );
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (path === '/api/health')
          return json({ google_configured: true, extension_configured: true });
        if (path === '/api/auth/google/exchange')
          return json({
            access_token: 'app-access-test-only',
            refresh_token: 'app-refresh-test-only',
            expires_in: 3600,
          });
        if (path === '/api/auth/extension/refresh') {
          state.refreshes++;
          return json({
            access_token: 'renewed-app-access-test-only',
            refresh_token: 'rotated-app-refresh-test-only',
            expires_in: 3600,
          });
        }
        if (path === '/api/auth/extension/logout') return json({ signed_out: true });
        if (path === '/api/me') return json(user);
        if (path === '/api/preferences')
          return json({ reminders_enabled: true, notification_enabled: true, blocked_senders: [] });
        if (path === '/api/extension/installation') return json({ registered: true });
        if (path === '/api/inbox/priority')
          return json(
            state.incoming
              ? [
                  {
                    ...reminder,
                    id: 'incoming-123',
                    direction: 'received',
                    notification_status: state.accepted
                      ? 'extension_notified'
                      : 'extension_pending',
                  },
                ]
              : [],
          );
        if (path === '/api/extension/reminders/incoming-123/notified') {
          state.accepted = true;
          return json({ accepted_by_chrome: true });
        }
        if (path === '/api/threads/import' || path === '/api/threads/resolve')
          return json({
            id: 'thread-123',
            subject: 'Project check-in',
            recipient_email: 'recipient@gmail.com',
          });
        if (path === '/api/threads/thread-123/eligibility')
          return json({
            status: state.mode === 'email' ? 'email_ready' : 'delivered',
            delivery_channel: state.mode,
            simulated: false,
            push_available: state.mode !== 'email',
            email_preview:
              state.mode === 'email'
                ? 'Hi, a gentle reminder about my earlier email. Thanks, Sender.'
                : null,
          });
        if (path === '/api/reminders' && init?.method === 'POST') {
          state.posts.push(body);
          return json(
            {
              ...reminder,
              delivery_channel: state.mode,
              status: state.mode === 'email' ? 'email_pending' : 'delivered',
            },
            201,
          );
        }
        if (path === '/api/reminders/reminder-123')
          return json({ ...reminder, delivery_channel: 'email', status: 'email_sent' });
        if (path === '/api/reminders') return json([]);
        throw new Error(`Unexpected extension request ${path}`);
      };
    });
    const read = () =>
      worker.evaluate(
        () => (globalThis as unknown as { nudgeTestState: MockState }).nudgeTestState,
      );
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize({ width: 390, height: 740 });
    await page.goto(`chrome-extension://${extensionId}/index.html?popup=1`);
    await expect(page.getByRole('button', { name: 'Connect Gmail', exact: true })).toBeEnabled();
    await expect(page.locator('.extension-privacy')).toContainText(
      'Google credentials stay encrypted on the server',
    );
    await page.getByRole('button', { name: 'Connect Gmail', exact: true }).click();
    await expect(page.getByText('Connected to Gmail', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Gmail', exact: true })).toBeVisible();
    expect((await read()).consent).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: 'test-results/previews/extension-popup.png', fullPage: true });

    await context.route('https://mail.google.com/**', (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><html><head><title>Gmail fixture</title></head><body style="font:16px Arial;padding:24px"><header aria-label="Google Account: Sender (sender@gmail.com)">Gmail · Sent</header><div role="main"><h2>Project check-in</h2><div data-legacy-thread-id="1111111111111111"><span class="gD" email="sender@gmail.com">Sender</span> to <span class="g2" email="recipient@gmail.com">Recipient</span></div><p>Original email content stays in Gmail.</p></div></body></html>',
      }),
    );
    const gmail = await context.newPage();
    await gmail.goto('https://mail.google.com/mail/u/0/#sent/1111111111111111');
    const { cdp, contextId } = await gmailContext(gmail);
    await expect.poll(() => shadowText(cdp)).toContain('Nudge');
    // The actual isolated content script cannot obtain credentials or invoke trusted actions.
    const secret = await cdp.send('Runtime.evaluate', {
      contextId,
      expression: "chrome.runtime.sendMessage({type:'EXTENSION_ACCESS_TOKEN'})",
      awaitPromise: true,
      returnByValue: true,
    });
    expect(secret.result.value).toMatchObject({ ok: false });
    expect(secret.result.value.token).toBeUndefined();
    const local = await cdp.send('Runtime.evaluate', {
      contextId,
      expression:
        '(async()=>{try{await chrome.storage.local.get(null);return false}catch{return true}})()',
      awaitPromise: true,
      returnByValue: true,
    });
    expect(local.result.value).toBe(true);
    const stale = await page.evaluate(() =>
      chrome.runtime.sendMessage({ type: 'DISCONNECT_EXTENSION', accountId: 'another-account' }),
    );
    expect(stale).toMatchObject({ ok: false });
    expect(stale.error).toContain('account changed');
    await expect(page.getByText('Connected to Gmail', { exact: true })).toBeVisible();
    await clickShadow(cdp, 'Nudge');
    await expect.poll(() => shadowText(cdp)).toContain('Eligible for a reminder');
    await clickShadow(cdp, 'Send reminder');
    await expect.poll(() => shadowText(cdp)).toContain('Reminder delivered');
    expect((await read()).posts.map((value) => value.delivery_channel)).toEqual(['in_app']);
    await clickShadow(cdp, 'Close');
    await worker.evaluate(() => {
      (globalThis as unknown as { nudgeTestState: MockState }).nudgeTestState.mode = 'email';
    });
    await clickShadow(cdp, 'Nudge');
    await expect.poll(() => shadowText(cdp)).toContain('This creates a new email');
    // A confirmation is bound to the Gmail tab that created it.
    const ticket = await worker.evaluate(async () =>
      Object.keys(await chrome.storage.session.get(null))
        .filter((key) => key.startsWith('pending:'))
        .at(-1)!
        .slice(8),
    );
    const other = await context.newPage();
    await other.goto('https://mail.google.com/mail/u/0/#sent/1111111111111111');
    const otherContext = await gmailContext(other);
    const tamper = await otherContext.cdp.send('Runtime.evaluate', {
      contextId: otherContext.contextId,
      expression: `chrome.runtime.sendMessage({type:'SUBMIT_REMINDER',ticket:${JSON.stringify(ticket)}})`,
      awaitPromise: true,
      returnByValue: true,
    });
    expect(tamper.result.value).toMatchObject({ ok: false });
    expect(tamper.result.value.error).toContain('confirmation expired or the account changed');
    await clickShadow(cdp, 'Send email reminder');
    await expect.poll(() => shadowText(cdp)).toContain('Email reminder sent');
    await gmail.screenshot({ path: 'test-results/previews/gmail-nudge.png', fullPage: true });

    await worker.evaluate(async () => {
      await chrome.storage.session.clear();
    });
    await page.reload();
    await expect(page.getByText('Connected to Gmail', { exact: true })).toBeVisible();
    expect((await read()).refreshes).toBe(1);
    await worker.evaluate(() => {
      (globalThis as unknown as { nudgeTestState: MockState }).nudgeTestState.incoming = true;
    });
    await page.reload();
    await expect.poll(async () => (await read()).notifications.length).toBe(1);
    await page.reload();
    await expect(page.getByText('Connected to Gmail', { exact: true })).toBeVisible();
    expect((await read()).notifications).toEqual([
      expect.objectContaining({
        message: 'Someone nudged an email conversation. Click to open it in Gmail.',
      }),
    ]);
    await page.getByText('Settings & privacy', { exact: true }).click();
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Connect Gmail', exact: true })).toBeVisible();
    expect(
      await worker.evaluate(async () => (await chrome.storage.local.get('connection')).connection),
    ).toBeUndefined();
    expect(errors).toEqual([]);
    // A real extension reload invalidates old Gmail scripts without reloading
    // the tab. Their timers/listeners must stop, and a tab refresh must recover.
    const reloadErrors: string[] = [];
    cdp.on('Runtime.exceptionThrown', (event) =>
      reloadErrors.push(
        event.exceptionDetails.exception?.description ?? event.exceptionDetails.text,
      ),
    );
    const manager = await context.newPage();
    await manager.goto('chrome://extensions');
    const developerMode = manager.locator('extensions-toolbar #devMode');
    if ((await developerMode.getAttribute('aria-pressed')) !== 'true') await developerMode.click();
    const extensionClosed = page.waitForEvent('close');
    await manager
      .locator(`extensions-item[id="${extensionId}"]`)
      .getByRole('button', { name: 'Reload', exact: true })
      .click();
    await extensionClosed;
    await expect
      .poll(async () => {
        const result = await cdp.send('Runtime.evaluate', {
          contextId,
          expression: '(()=>{try{return Boolean(chrome.runtime?.id)}catch{return false}})()',
          returnByValue: true,
        });
        return result.result.value;
      })
      .toBe(false);
    await gmail.evaluate(() => {
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      document.dispatchEvent(new Event('visibilitychange'));
      document.body.append(document.createElement('span'));
    });
    await expect(gmail.locator('#inbox-nudge-root')).toHaveCount(0);
    expect(reloadErrors).toEqual([]);
    // The old context dies before Chrome finishes loading the replacement.
    // Wait for a fresh extension page before testing Gmail reinjection.
    const replacement = await context.newPage();
    await expect(async () => {
      await replacement.goto(`chrome-extension://${extensionId}/index.html`);
      expect(await replacement.evaluate(() => chrome.runtime.id)).toBe(extensionId);
    }).toPass({ timeout: 10000 });
    await gmail.reload();
    await expect.poll(() => shadowText(cdp)).toContain('Connect Inbox Nudge');
    expect(reloadErrors).toEqual([]);
  } finally {
    await context.close();
    await rm(profile, { recursive: true, force: true });
  }
});
