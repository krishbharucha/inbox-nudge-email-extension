import { useEffect, useState } from 'react';
import { BellOff, Check, LoaderCircle, Mail } from 'lucide-react';
import { request } from './api';

export default function EmailPreferences({ token }: { token: string }) {
  const [sender, setSender] = useState('');
  const [blocked, setBlocked] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const path = `/email/preferences/${encodeURIComponent(token)}`;
  useEffect(() => {
    request<{ sender_name: string; blocked: boolean }>(path)
      .then((value) => {
        setSender(value.sender_name);
        setBlocked(value.blocked);
      })
      .catch(() => setError('This email preference link is invalid or no longer available.'))
      .finally(() => setBusy(false));
  }, [path]);
  async function stop() {
    setBusy(true);
    setError('');
    try {
      await request(`${path}/unsubscribe`, { method: 'POST' });
      setBlocked(true);
    } catch {
      setError('Unable to update your email preference. Please try again.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="email-preferences-page">
      <section className="panel">
        <span className="modal-illustration">
          <Mail size={28} />
        </span>
        <p className="section-kicker">INBOX NUDGE</p>
        <h1>Email reminder preferences</h1>
        {sender && <p>Reminders from {sender}</p>}
        {error && (
          <p className="notification-error" role="alert">
            {error}
          </p>
        )}
        {blocked ? (
          <p role="status" className="notification-enabled">
            <Check size={17} />
            Email reminders from this sender are stopped.
          </p>
        ) : (
          <>
            <p>
              Stop future Inbox Nudge email reminders from this sender. No account or sign-in is
              required.
            </p>
            <button className="button primary" disabled={busy || !sender} onClick={stop}>
              {busy ? <LoaderCircle className="spin" size={16} /> : <BellOff size={16} />}Stop email
              reminders
            </button>
          </>
        )}
      </section>
    </main>
  );
}
