import { useCallback, useEffect, useState } from 'react';
import {
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  LoaderCircle,
  Mail,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import type { Preferences, Reminder, User } from './types';
import { statusLabels } from './types';
import './extension.css';

interface State {
  health: { google_configured: boolean; extension_configured: boolean };
  user: User | null;
  preferences?: Preferences;
  history?: Reminder[];
  alertsAllowed?: boolean;
}

async function action<T>(message: Record<string, unknown>): Promise<T> {
  const result = await chrome.runtime.sendMessage(message);
  if (!result?.ok)
    throw new Error(
      result?.error || 'Unable to reach Inbox Nudge. Reopen the extension to try again.',
    );
  return result as T;
}

export default function ExtensionApp() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [blockedEmail, setBlockedEmail] = useState('');
  const popup = new URLSearchParams(location.search).has('popup');
  const load = useCallback(async () => {
    setState(await action<State>({ type: 'EXTENSION_STATE' }));
  }, []);
  useEffect(() => {
    load().catch(() => setError('Inbox Nudge cannot reach its service. Try again shortly.'));
  }, [load]);
  async function perform(work: () => Promise<unknown>, message = '') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await work();
      await load();
      setNotice(message);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Please try again.');
      await load().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }
  async function save(preferences: Preferences) {
    await perform(
      () => accountAction({ type: 'SAVE_EXTENSION_PREFERENCES', preferences }),
      'Preferences saved.',
    );
  }
  const user = state?.user;
  function accountAction<T>(message: Record<string, unknown>) {
    if (!user) throw new Error('Reconnect Gmail before changing account settings.');
    return action<T>({ ...message, accountId: user.id });
  }
  const preferences = state?.preferences;
  const connect = () =>
    perform(
      () => action({ type: 'CONNECT_GOOGLE', emailReminders: true }),
      'Gmail connected. You’re ready to nudge.',
    );
  const openGmail = () => perform(() => action({ type: 'OPEN_GMAIL' }));
  return (
    <main className={`extension-app ${popup ? 'extension-popup' : ''}`}>
      <header className="extension-brand">
        <img src="/icon.svg" width="34" height="34" alt="" />
        <strong>
          inboxnudge<span>.</span>
        </strong>
        <span className="extension-brand-tag">FOR GMAIL</span>
      </header>
      {!state && !error ? (
        <div className="extension-loading">
          <LoaderCircle className="spin" size={22} />
          <p>Checking your connection…</p>
        </div>
      ) : user ? (
        <>
          <div className="extension-connected">
            <span>
              <Check size={14} />
              Connected to Gmail
            </span>
            <p>{user.email}</p>
          </div>
          <h1>
            A little nudge.
            <br />
            <em>Right in Gmail.</em>
          </h1>
          <p className="extension-lead">
            Open a sent conversation and click <strong>Nudge</strong>. That’s your everyday
            workflow.
          </p>
          <button className="extension-primary" disabled={busy} onClick={openGmail}>
            <Mail size={17} />
            Open Gmail
            <ArrowUpRight size={16} />
          </button>
          <p className="extension-small">
            {user.daily_used} of {user.daily_limit} requests used in the past 24 hours.
          </p>
          <details className="extension-settings">
            <summary>
              Settings & privacy
              <ChevronDown size={15} />
            </summary>
            {preferences && (
              <>
                <label className="extension-toggle">
                  <span>
                    Receive nudges<small>Allow reminders from other people.</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={preferences.reminders_enabled}
                    disabled={busy}
                    onChange={(e) => save({ ...preferences, reminders_enabled: e.target.checked })}
                  />
                </label>
                <label className="extension-toggle">
                  <span>
                    Chrome notifications
                    <small>Alerts use Chrome while it’s running. Device settings apply.</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={preferences.notification_enabled}
                    disabled={busy}
                    onChange={(e) =>
                      save({ ...preferences, notification_enabled: e.target.checked })
                    }
                  />
                </label>
                {!state.alertsAllowed && (
                  <p className="extension-small">
                    Chrome notifications are blocked. Allow them in your browser or system settings
                    to receive alerts.
                  </p>
                )}
                <form
                  className="extension-block"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (blockedEmail.trim())
                      void save({
                        ...preferences,
                        blocked_senders: [
                          ...new Set([
                            ...preferences.blocked_senders,
                            blockedEmail.trim().toLowerCase(),
                          ]),
                        ],
                      }).then(() => setBlockedEmail(''));
                  }}
                >
                  <label htmlFor="block-email">Block a sender</label>
                  <div>
                    <input
                      id="block-email"
                      type="email"
                      placeholder="name@example.com"
                      value={blockedEmail}
                      onChange={(e) => setBlockedEmail(e.target.value)}
                      maxLength={320}
                      required
                    />
                    <button disabled={busy}>Block</button>
                  </div>
                </form>
                {preferences.blocked_senders.map((email) => (
                  <div className="extension-blocked" key={email}>
                    <span>{email}</span>
                    <button
                      disabled={busy}
                      aria-label={`Unblock ${email}`}
                      onClick={() =>
                        save({
                          ...preferences,
                          blocked_senders: preferences.blocked_senders.filter(
                            (value) => value !== email,
                          ),
                        })
                      }
                    >
                      Unblock
                    </button>
                  </div>
                ))}
              </>
            )}
            <div className="extension-privacy">
              <ShieldCheck size={18} />
              <p>
                Google credentials stay encrypted on our server. We store conversation metadata, not
                email bodies. Nudges use a verified conversation and respect recipient preferences.
              </p>
            </div>
            <p className="extension-small">
              {user.gmail_send_enabled
                ? 'Gmail read and send access. Every email reminder needs your confirmation.'
                : 'Gmail read-only access. Connect again to enable the optional email fallback.'}
            </p>
            <div className="extension-account-actions">
              <button disabled={busy} onClick={connect}>
                Reconnect Gmail
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  perform(
                    () => accountAction({ type: 'SIGN_OUT_EXTENSION' }),
                    'Signed out of this Chrome profile.',
                  )
                }
              >
                Sign out
              </button>
            </div>
            <button
              className="extension-disconnect"
              disabled={busy}
              onClick={() =>
                perform(async () => {
                  const result = await accountAction<{ googleRevoked: boolean }>({
                    type: 'DISCONNECT_EXTENSION',
                  });
                  if (!result.googleRevoked)
                    throw new Error(
                      'Local credentials were removed. Also remove Inbox Nudge in Google Account → Third-party connections to confirm revocation.',
                    );
                }, 'Google disconnected and stored credentials removed.')
              }
            >
              Disconnect Google & remove credentials
            </button>
          </details>
          {!!state.history?.length && (
            <details className="extension-settings">
              <summary>
                Recent nudges
                <ChevronDown size={15} />
              </summary>
              {state.history.map((reminder) => (
                <div className="extension-recent" key={reminder.id}>
                  <span>{reminder.subject}</span>
                  <small>{statusLabels[reminder.status]}</small>
                  {reminder.status === 'email_unknown' && reminder.direction === 'sent' && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        perform(() =>
                          accountAction({ type: 'CHECK_EMAIL_STATUS', id: reminder.id }),
                        )
                      }
                    >
                      Check Gmail Sent
                    </button>
                  )}
                </div>
              ))}
            </details>
          )}
        </>
      ) : (
        <>
          <span className="extension-kicker">LESS CHASING. MORE CONNECTING.</span>
          <h1>
            A little nudge.
            <br />
            <em>Right in Gmail.</em>
          </h1>
          <p className="extension-lead">
            Connect Gmail once. Open a conversation in Sent. Click <strong>Nudge</strong>.
          </p>
          <div className="extension-steps">
            <span>
              1<small>Connect Gmail</small>
            </span>
            <i />
            <span>
              2<small>Open Sent</small>
            </span>
            <i />
            <span>
              3<small>Click Nudge</small>
            </span>
          </div>
          <button
            className="extension-primary"
            disabled={
              busy || !state?.health.google_configured || !state?.health.extension_configured
            }
            onClick={connect}
          >
            {busy ? <LoaderCircle className="spin" size={17} /> : <Mail size={17} />}Connect Gmail
            <ArrowUpRight size={16} />
          </button>
          {state && !state.health.extension_configured && (
            <p className="extension-small">
              This local build needs its Chrome extension ID registered with the service before
              connecting. See the setup guide included with the project.
            </p>
          )}
          <div className="extension-privacy">
            <ShieldCheck size={18} />
            <p>
              Google asks for permission to verify your conversations and send confirmed email
              reminders. Google credentials stay encrypted on the server; email bodies aren’t
              stored.
            </p>
          </div>
          <p className="extension-small">
            Connected recipients can receive a Chrome alert. Other recipients need a confirmed email
            reminder. No website visit is required to use the extension.
          </p>
        </>
      )}
      {error && (
        <p className="extension-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="extension-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <button className="extension-retry" disabled={busy} onClick={() => perform(load)}>
          <RefreshCw size={14} />
          Try again
        </button>
      )}
      <footer className="extension-footer">
        <Bell size={13} />A thoughtful follow-up, one click away.
      </footer>
    </main>
  );
}
