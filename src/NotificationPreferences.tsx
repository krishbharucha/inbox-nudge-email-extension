import { useEffect, useState } from 'react';
import { Bell, BellOff, ExternalLink, LoaderCircle } from 'lucide-react';
import { isExtension, request } from './api';

interface PushConfig {
  configured: boolean;
  public_key: string;
  subscriptions: number;
  web_app_origin: string;
}

export default function NotificationPreferences() {
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [registered, setRegistered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const supported =
    !isExtension &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window &&
    window.isSecureContext;
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const value = await request<PushConfig>('/notifications/config');
        if (cancelled) return;
        setConfig(value);
        if (supported) {
          const registration = await navigator.serviceWorker.getRegistration('/');
          const subscription = await registration?.pushManager.getSubscription();
          if (!cancelled) setRegistered(Boolean(subscription && value.subscriptions));
        }
      } catch (failure) {
        if (!cancelled)
          setError(
            failure instanceof Error ? failure.message : 'Unable to load notification settings.',
          );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supported]);

  async function enable() {
    if (!config) return;
    setBusy(true);
    setError('');
    try {
      // Invoke permission directly from the click, before any other await.
      const permission = await Notification.requestPermission();
      if (permission !== 'granted')
        throw new Error(
          'Notifications weren’t allowed. You can change this in your browser’s site settings.',
        );
      await navigator.serviceWorker.register('/push-sw.js', { scope: '/' });
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        const encoded = config.public_key.replace(/-/g, '+').replace(/_/g, '/');
        const bytes = Uint8Array.from(
          atob(encoded + '='.repeat((4 - (encoded.length % 4)) % 4)),
          (c) => c.charCodeAt(0),
        );
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: bytes.buffer,
        });
      }
      await request('/notifications/subscriptions', {
        method: 'POST',
        body: JSON.stringify(subscription.toJSON()),
      });
      setRegistered(true);
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : 'Unable to enable browser notifications.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function disable(all = false) {
    setBusy(true);
    setError('');
    try {
      const registration = supported
        ? await navigator.serviceWorker.getRegistration('/')
        : undefined;
      const subscription = await registration?.pushManager.getSubscription();
      if (all) await request('/notifications/subscriptions/remove-all', { method: 'POST' });
      else if (subscription)
        await request('/notifications/subscriptions/remove', {
          method: 'POST',
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
      await subscription?.unsubscribe();
      setRegistered(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to disable notifications.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel google-connection-panel">
      <div className="blocked-section">
        <h3>Browser notifications</h3>
        <p>
          Receive Inbox Nudge alerts when this website’s tab is closed. Your browser and device
          settings control whether alerts appear.
        </p>
        {registered && (
          <p className="notification-enabled">
            <Bell size={14} />
            This browser is registered. Your reminder preferences still apply.
          </p>
        )}
        {error && (
          <p className="notification-error" role="alert">
            {error}
          </p>
        )}
        <div className="connection-buttons">
          {isExtension && config ? (
            <a
              className="button secondary"
              href={config.web_app_origin}
              target="_blank"
              rel="noreferrer"
            >
              Enable on our website
              <ExternalLink size={14} />
            </a>
          ) : registered ? (
            <button className="button secondary" disabled={busy} onClick={() => disable()}>
              <BellOff size={15} />
              Disable on this browser
            </button>
          ) : (
            <button
              className="button primary"
              disabled={busy || !supported || !config?.configured}
              onClick={enable}
            >
              {busy ? <LoaderCircle className="spin" size={15} /> : <Bell size={15} />}Enable
              notifications
            </button>
          )}
          <button className="text-button" disabled={busy || !config} onClick={() => disable(true)}>
            Remove all registered browsers
          </button>
        </div>
        {!isExtension && !supported && (
          <p>
            This browser doesn’t support website push here. Use a supported browser on HTTPS or
            localhost.
          </p>
        )}
        {config && !config.configured && (
          <p>Browser push needs the server’s notification keys configured.</p>
        )}
      </div>
    </section>
  );
}
