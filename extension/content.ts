import { canShowReminder, resolveGmailReference, sentRoute } from './gmail';
import { createContentRuntime } from './content-runtime';
import {
  statusDetails,
  statusLabels,
  type Preferences,
  type Reminder,
  type Status,
} from '../src/types';

type Context = {
  ok: boolean;
  email: string;
  demo: boolean;
  preferences: Preferences;
  reminders: Reminder[];
};
const host = document.createElement('div');
host.id = 'inbox-nudge-root';
const shadow = host.attachShadow({ mode: 'closed' });
const style = document.createElement('style');
style.textContent = `:host{display:block;font:13px 'Segoe UI',sans-serif;color:#254633}.bar{margin:12px 0;padding:12px 16px;border:1px solid #dbe6ce;background:#f3f7ec;border-radius:8px;display:flex;align-items:center;gap:14px}.bar span{flex:1;line-height:1.7}.bar button,button.nudge{font:500 12px 'Segoe UI',sans-serif;border:1px solid #d5e1c7;border-radius:6px;padding:9px 13px;cursor:pointer;background:white;color:#3f6445}button.nudge{margin:8px 0;background:#244d38;color:white}dialog{border:1px solid #dce5d1;border-radius:12px;padding:25px;color:#34543b;width:390px;max-width:90vw;font:13px 'Segoe UI',sans-serif}dialog::backdrop{background:#14271d66}dialog h2{font:24px Georgia,serif;margin:0 0 18px}dialog p{line-height:1.8;overflow-wrap:anywhere}dialog button{padding:9px 12px;margin:12px 8px 0 0;border:1px solid #dce5d0;border-radius:5px;background:#edf3e5;color:#3f6445;cursor:pointer}`;
shadow.append(style);
let context: Context | null = null;
let lastSignature = '';
let scheduled = false;
let requestedMailbox: string | null = null;
const runtime = createContentRuntime(() => {
  context = null;
  host.remove();
  document.querySelectorAll('[data-inbox-nudge-indicator]').forEach((el) => el.remove());
});

function askContext() {
  if (!runtime.active()) return;
  const mailbox = accountEmail();
  requestedMailbox = mailbox;
  void runtime.send<Context>({ type: 'GMAIL_CONTEXT', mailbox }).then((response) => {
    if (!runtime.active() || accountEmail() !== mailbox) return;
    context = response?.ok ? response : null;
    render();
    markInboxRows();
  });
}

function text(tag: string, value: string) {
  const el = document.createElement(tag);
  el.textContent = value;
  return el;
}

function accountEmail() {
  const account =
    document.querySelector('[aria-label*="Google Account"]')?.getAttribute('aria-label') ?? '';
  return account.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0] ?? null;
}

function render() {
  if (!runtime.active()) return;
  const main = document.querySelector('div[role="main"]');
  if (!main) {
    host.remove();
    lastSignature = '';
    return;
  }
  const subject = main.querySelector('h2')?.textContent?.trim() ?? '';
  const senders = Array.from(main.querySelectorAll<HTMLElement>('.gD[email]'))
    .map((e) => e.getAttribute('email')!)
    .filter(Boolean);
  const recipients = [
    ...new Set(
      Array.from(main.querySelectorAll<HTMLElement>('.g2[email]'))
        .map((e) => e.getAttribute('email')!)
        .filter(Boolean),
    ),
  ];
  const self = accountEmail();
  if (self && self !== requestedMailbox) {
    askContext();
    return;
  }
  if (!context || self?.toLowerCase() !== context.email.toLowerCase()) {
    const signature = JSON.stringify(['connect', location.hash, self, subject]);
    if (signature === lastSignature && host.isConnected) return;
    lastSignature = signature;
    shadow.querySelectorAll(':scope > :not(style)').forEach((el) => el.remove());
    host.remove();
    if (self && subject && sentRoute(location.hash)) {
      const button = text('button', 'Connect Inbox Nudge');
      button.className = 'nudge';
      button.addEventListener('click', () => void runtime.send({ type: 'OPEN_EXTENSION' }));
      shadow.append(button);
      main.prepend(host);
    }
    return;
  }
  const reference = resolveGmailReference({
    hash: location.hash,
    threadIds: Array.from(main.querySelectorAll('[data-legacy-thread-id]')).map(
      (el) => el.getAttribute('data-legacy-thread-id') ?? '',
    ),
    messageIds: Array.from(main.querySelectorAll('[data-legacy-message-id]')).map(
      (el) => el.getAttribute('data-legacy-message-id') ?? '',
    ),
  });
  const eligible = canShowReminder({
    hash: location.hash,
    accountEmail: self,
    authenticatedEmail: context.email,
    senderEmails: senders,
    recipientEmails: recipients,
    subject,
  });
  const threadId = location.hash.split('/').at(-1);
  const matched =
    !context.demo && self?.toLowerCase() === context.email.toLowerCase()
      ? context.reminders.find(
          (r) =>
            !r.simulated &&
            (r.provider_thread_id === threadId ||
              (reference?.kind === 'thread' && r.provider_thread_id === reference.id)),
        )
      : undefined;
  const signature = JSON.stringify([
    location.hash,
    self,
    eligible,
    matched?.id,
    context.preferences.notification_enabled,
    reference?.id,
  ]);
  if (signature === lastSignature && host.isConnected) return;
  lastSignature = signature;
  shadow.querySelectorAll(':scope > :not(style)').forEach((el) => el.remove());
  host.remove();
  if (!eligible && !matched) return;
  main.prepend(host);
  if (matched && context.preferences.notification_enabled) {
    const banner = document.createElement('div');
    banner.className = 'bar';
    banner.append(
      text('span', `${matched.sender_name} sent a gentle reminder about this conversation.`),
    );
    const dismiss = text('button', 'Dismiss');
    dismiss.addEventListener('click', async () => {
      (dismiss as HTMLButtonElement).disabled = true;
      const result = await runtime.send({
        type: 'DISMISS_GMAIL_REMINDER',
        mailbox: self,
        id: matched.id,
      });
      if (!runtime.active()) return;
      if (result?.ok) askContext();
      else {
        dismiss.textContent = 'Try again';
        (dismiss as HTMLButtonElement).disabled = false;
      }
    });
    banner.append(dismiss);
    shadow.append(banner);
  }
  if (eligible) {
    const button = text('button', 'Nudge');
    button.className = 'nudge';
    button.addEventListener('click', async () => {
      if (!runtime.active()) return;
      const dialog = document.createElement('dialog');
      dialog.append(
        text('h2', 'Send a nudge?'),
        text('p', `To: ${recipients[0]}`),
        text('p', `Original subject: ${subject}`),
      );
      const status = text('p', 'Checking the conversation with Gmail…');
      const send = text('button', 'Send reminder') as HTMLButtonElement;
      send.disabled = true;
      const open = text('button', 'Extension settings');
      open.addEventListener('click', () => {
        void runtime.send({ type: 'OPEN_EXTENSION' });
        dialog.close();
      });
      const close = text('button', 'Close');
      close.addEventListener('click', () => dialog.close());
      dialog.addEventListener('close', () => dialog.remove());
      dialog.append(status, send, open, close);
      shadow.append(dialog);
      dialog.showModal();
      type Prepared = {
        ok: boolean;
        error?: string;
        thread?: { subject: string; recipient_email: string };
        status: Status;
        ticket: string;
        email_preview?: string | null;
      };
      const result = await runtime.send<Prepared>({
        type: 'PREPARE_REMINDER',
        mailbox: self,
        reference,
        subject,
        recipient: recipients[0],
      });
      if (!runtime.active() || !dialog.isConnected) return;
      if (!result.ok || !result.thread) {
        status.textContent = result.error || 'Unable to verify eligibility.';
        return;
      }
      const paragraphs = dialog.querySelectorAll('p');
      paragraphs[0].textContent = `To: ${result.thread.recipient_email}`;
      paragraphs[1].textContent = `Original subject: ${result.thread.subject}`;
      status.textContent = `${result.status === 'delivered' ? 'Eligible for a reminder' : statusLabels[result.status]}. ${statusDetails[result.status]}`;
      if (result.email_preview) {
        const preview = text(
          'pre',
          result.email_preview +
            '\n\nAn unsubscribe link will be included. This creates a new email.',
        );
        preview.setAttribute(
          'style',
          'white-space:pre-wrap;font:12px/1.7 sans-serif;background:#f2f6ec;padding:12px;border-radius:6px;',
        );
        status.after(preview);
      }
      send.disabled = !['delivered', 'recipient_not_connected', 'email_ready'].includes(
        result.status,
      );
      if (result.status === 'email_ready') send.textContent = 'Send email reminder';
      if (result.status === 'recipient_not_connected') send.textContent = 'Record request';
      send.addEventListener('click', async () => {
        send.disabled = true;
        status.textContent = 'Submitting and rechecking eligibility…';
        const response = await runtime.send<{ ok: boolean; error?: string; reminder?: Reminder }>({
          type: 'SUBMIT_REMINDER',
          ticket: result.ticket,
        });
        if (!runtime.active() || !dialog.isConnected) return;
        if (!response.ok || !response.reminder) {
          status.textContent = response.error || 'Unable to submit the reminder.';
          send.disabled = false;
          return;
        }
        const showStatus = (reminder: Reminder) => {
          status.textContent = `${statusLabels[reminder.status]}. ${statusDetails[reminder.status]}`;
          if (reminder.notification_status === 'push_accepted')
            status.textContent +=
              ' The browser push service accepted the alert; device settings control display.';
          if (reminder.notification_status === 'failed')
            status.textContent +=
              ' The browser notification could not be sent. The reminder is available in Inbox Nudge.';
          if (reminder.notification_status === 'extension_pending')
            status.textContent +=
              ' The recipient’s extension will check for the alert while Chrome is running.';
          if (reminder.notification_status === 'extension_notified')
            status.textContent +=
              ' Chrome accepted the alert; device settings control its display.';
        };
        showStatus(response.reminder);
        send.remove();
        if (
          ['email_pending', 'email_sending'].includes(response.reminder.status) ||
          ['queued', 'sending'].includes(response.reminder.notification_status ?? '')
        ) {
          let polling = false,
            attempts = 0;
          const cancelPolling = runtime.interval(async () => {
            if (!dialog.isConnected || ++attempts > 30) {
              cancelPolling();
              return;
            }
            if (polling) return;
            polling = true;
            try {
              const updated = await runtime.send<{ ok: boolean; reminder?: Reminder }>({
                type: 'REMINDER_STATUS',
                ticket: result.ticket,
              });
              if (runtime.active() && dialog.isConnected && updated.ok && updated.reminder) {
                showStatus(updated.reminder);
                if (
                  !['email_pending', 'email_sending'].includes(updated.reminder.status) &&
                  !['queued', 'sending'].includes(updated.reminder.notification_status ?? '')
                )
                  cancelPolling();
              }
            } catch {
              /* Status checks never retry submission. */
            } finally {
              polling = false;
            }
          }, 2000);
          dialog.addEventListener('close', cancelPolling, { once: true });
        }
      });
    });
    shadow.append(button);
  }
}

const observer = new MutationObserver(() => {
  if (!runtime.active() || scheduled) return;
  scheduled = true;
  runtime.timeout(() => {
    scheduled = false;
    render();
    markInboxRows();
  }, 350);
});
observer.observe(document.body, { childList: true, subtree: true });
runtime.onDispose(() => observer.disconnect());
const onHashChange = () => {
  lastSignature = '';
  render();
};
window.addEventListener('hashchange', onHashChange);
runtime.onDispose(() => window.removeEventListener('hashchange', onHashChange));
const onVisibilityChange = () => {
  if (!document.hidden) askContext();
};
document.addEventListener('visibilitychange', onVisibilityChange);
runtime.onDispose(() => document.removeEventListener('visibilitychange', onVisibilityChange));
runtime.interval(askContext, 60000);
const onMessage = (message: { type?: string }) => {
  if (message?.type === 'CONNECTION_CHANGED') askContext();
};
if (runtime.active()) {
  try {
    chrome.runtime.onMessage.addListener(onMessage);
    runtime.onDispose(() => chrome.runtime.onMessage.removeListener(onMessage));
  } catch {
    runtime.dispose();
  }
}
askContext();

function markInboxRows() {
  if (!runtime.active()) return;
  const self = accountEmail();
  const reminders =
    context &&
    !context.demo &&
    context.preferences.notification_enabled &&
    self?.toLowerCase() === context.email.toLowerCase()
      ? context.reminders.filter((r) => !r.simulated)
      : [];
  document.querySelectorAll<HTMLElement>('[data-inbox-nudge-indicator]').forEach((el) => {
    if (!reminders.some((r) => r.id === el.dataset.inboxNudgeIndicator)) el.remove();
  });
  document.querySelectorAll('[role="row"][data-legacy-thread-id]').forEach((row) => {
    const reminder = reminders.find(
      (r) => r.provider_thread_id === row.getAttribute('data-legacy-thread-id'),
    );
    if (!reminder || row.querySelector('[data-inbox-nudge-indicator]')) return;
    const badge = text('span', 'Nudged');
    badge.setAttribute('data-inbox-nudge-indicator', reminder.id);
    badge.setAttribute('title', 'A reminder is available in Inbox Nudge');
    badge.setAttribute(
      'style',
      'display:inline-block;margin:0 6px;padding:2px 6px;border-radius:4px;background:#eaf2df;color:#4c6f3a;font-size:10px;',
    );
    (row.querySelector('[role="gridcell"]') ?? row).append(badge);
  });
}
