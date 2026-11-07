import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BellOff,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  ExternalLink,
  Inbox,
  LayoutGrid,
  LoaderCircle,
  LogOut,
  Mail,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react';
import { api, ApiError, isExtension, openWorkspace, request, setToken } from './api';
import GmailInbox from './GmailInbox';
import NotificationPreferences from './NotificationPreferences';
import {
  statusDetails,
  statusLabels,
  type Preferences,
  type Reminder,
  type Status,
  type Thread,
  type User,
  type DeliveryPlan,
} from './types';

type View = 'overview' | 'gmail' | 'priority' | 'sent' | 'history' | 'settings';
type Dialog =
  | { kind: 'thread'; thread: Thread }
  | { kind: 'reminder'; reminder: Reminder }
  | { kind: 'help' }
  | null;
const views = {
  gmail: ['Your Gmail inbox.', 'Your everyday conversations, connected to Inbox Nudge.'],
  overview: [
    'Your inbox, a little lighter.',
    'A gentle reminder for the conversations that matter.',
  ],
  priority: ['The conversations that matter.', 'A little attention can make someone’s day.'],
  sent: ['A little nudge goes a long way.', 'Bring an unanswered conversation back into focus.'],
  history: ['Every nudge, in one place.', 'An honest record of what happened to your reminders.'],
  settings: ['Your inbox. Your boundaries.', 'Make Inbox Nudge work the way you do.'],
};

function initials(name: string) {
  return name
    .split(/[ .@]+/)
    .slice(0, 2)
    .map((s) => s[0])
    .join('')
    .toUpperCase();
}

function relativeTime(value: string) {
  const date = new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

function Logo({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand ${small ? 'small' : ''}`}>
      <span className="brand-icon">
        <Mail size={22} strokeWidth={1.7} />
        <span className="brand-rays">⌁</span>
      </span>
      <span>
        inbox<span className="brand-light">nudge</span>
        <span className="brand-dot">.</span>
      </span>
    </div>
  );
}

function Avatar({ name, size = '' }: { name: string; size?: string }) {
  const color = name.startsWith('M')
    ? 'peach'
    : name.startsWith('J')
      ? 'purple'
      : name.startsWith('S')
        ? 'blue'
        : 'green';
  return <span className={`avatar ${color} ${size}`}>{initials(name)}</span>;
}

function StatusPill({ status, simulated = false }: { status: Status; simulated?: boolean }) {
  return (
    <span
      className={`status-pill ${['delivered', 'email_sent'].includes(status) ? 'success' : status === 'dismissed' ? 'neutral' : 'warning'}`}
    >
      <span className="status-dot" />
      {status === 'delivered'
        ? simulated
          ? 'Delivered · demo'
          : 'Delivered'
        : status === 'recipient_not_connected'
          ? 'Not connected'
          : status === 'cooldown_active'
            ? 'Cooldown'
            : status === 'dismissed'
              ? 'Dismissed'
              : statusLabels[status]}
    </span>
  );
}

function Modal({
  children,
  onClose,
  title,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    el?.focus();
    function trap(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab' && el) {
        const items = Array.from(
          el.querySelectorAll<HTMLElement>('button:not(:disabled), input, a[href], [tabindex="0"]'),
        );
        const first = items[0],
          last = items[items.length - 1];
        if (!items.length) {
          event.preventDefault();
          el.focus();
        } else if (
          event.shiftKey &&
          (document.activeElement === first || document.activeElement === el)
        ) {
          event.preventDefault();
          last.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last || document.activeElement === el)
        ) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <button className="icon-button modal-close" aria-label="Close dialog" onClick={onClose}>
          <X size={19} />
        </button>
        {children}
      </div>
    </div>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Inbox size={28} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [priority, setPriority] = useState<Reminder[]>([]);
  const [history, setHistory] = useState<Reminder[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [view, setView] = useState<View>('overview');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [dialog, setDialog] = useState<Dialog>(null);
  const [filter, setFilter] = useState<'all' | 'sent' | 'received'>('all');
  const [blockedEmail, setBlockedEmail] = useState('');
  const [sessionExpired, setSessionExpired] = useState(false);
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [googleConfigured, setGoogleConfigured] = useState(false);
  const [nextPage, setNextPage] = useState<string | undefined>();
  const [result, setResult] = useState<Reminder | null>(null);
  const [eligible, setEligible] = useState<Status | null>(null);
  const [deliveryPlan, setDeliveryPlan] = useState<DeliveryPlan | null>(null);
  const [checking, setChecking] = useState(false);
  const idempotency = useRef(crypto.randomUUID());
  const initial = useRef(false);

  const load = useCallback(async () => {
    const [account, inbox, recent, sent, preferences] = await Promise.all([
      api.me(),
      api.priority(),
      api.history(),
      api.threads(),
      api.preferences(),
    ]);
    setUser(account);
    setPriority(inbox);
    setHistory(recent);
    setThreads(sent);
    setPrefs(preferences);
    return account;
  }, []);

  const endSession = useCallback(() => {
    setUser(null);
    setSessionExpired(true);
  }, []);

  useEffect(() => {
    if (initial.current) return;
    initial.current = true;
    (async () => {
      try {
        const health = await request<{
          demo_mode: boolean;
          google_configured: boolean;
          extension_configured: boolean;
        }>('/health');
        setDemoAvailable(health.demo_mode);
        setGoogleConfigured(
          health.google_configured && (!isExtension || health.extension_configured),
        );
        await api.completeGoogleRedirect();
        try {
          const account = await load();
          if (!account.demo) {
            const desired =
              new URLSearchParams(location.search).get('view') ||
              sessionStorage.getItem('nudge-view');
            setView(desired === 'priority' ? 'priority' : 'gmail');
            sessionStorage.removeItem('nudge-view');
          }
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) setSessionExpired(false);
          else throw e;
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unable to connect to the API.');
      } finally {
        setLoading(false);
      }
    })();
  }, [load]);

  // Keep the open workspace current alongside background notification delivery.
  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => {
      load().catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setUser(null);
          setSessionExpired(true);
        }
      });
    }, 30000);
    return () => clearInterval(timer);
  }, [user, load]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (
      !result ||
      !(
        ['email_pending', 'email_sending'].includes(result.status) ||
        ['queued', 'sending'].includes(result.notification_status ?? '')
      )
    )
      return;
    let cancelled = false;
    const id = result.id;
    let inFlight = false;
    const timer = setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const updated = await api.reminder(id);
        if (!cancelled) {
          setResult(updated);
          if (
            !['email_pending', 'email_sending'].includes(updated.status) &&
            !['queued', 'sending'].includes(updated.notification_status ?? '')
          )
            await load();
        }
      } catch {
        /* Keep the durable status visible; do not retry the send. */
      } finally {
        inFlight = false;
      }
    }, 1250);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [result, load]);

  const closeDialog = useCallback(() => {
    setDialog(null);
    setResult(null);
    setEligible(null);
    setDeliveryPlan(null);
  }, []);

  async function perform(action: () => Promise<unknown>, success?: string) {
    setBusy(true);
    setError('');
    try {
      await action();
      await load();
      if (success) setNotice(success);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setUser(null);
        setSessionExpired(true);
      }
      setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function login(persona: 'alex' | 'maya') {
    setNextPage(undefined);
    setView('overview');
    await perform(() => api.login(persona));
  }

  async function syncSent(pageToken?: string) {
    await perform(async () => {
      const result = await api.sync(pageToken);
      setNextPage(result.next_page_token ?? undefined);
      setNotice(
        `Synced ${result.synced} conversations. ${result.skipped ? `${result.skipped} unsupported conversations skipped.` : ''}`,
      );
    });
  }

  function gmailLink(id: string) {
    return `https://mail.google.com/mail/u/?authuser=${encodeURIComponent(user?.email ?? '')}#all/${encodeURIComponent(id)}`;
  }

  async function disconnectGoogle() {
    setBusy(true);
    setError('');
    try {
      const result = await api.disconnect();
      await setToken(null);
      setUser(null);
      if (!result.google_revoked)
        setError(
          'Disconnected from Inbox Nudge. Google could not confirm revocation; remove access in Google Account → Third-party connections.',
        );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to disconnect.');
    } finally {
      setBusy(false);
    }
  }

  async function confirmThread(thread: Thread) {
    setDialog({ kind: 'thread', thread });
    setResult(null);
    setEligible(null);
    setDeliveryPlan(null);
    setChecking(true);
    idempotency.current = crypto.randomUUID();
    try {
      const plan = await api.eligibility(thread.id);
      setEligible(plan.status);
      setDeliveryPlan(plan);
    } catch {
      setEligible('unable_to_verify');
    } finally {
      setChecking(false);
    }
  }

  function navigate(next: View) {
    setView(next);
    setQuery('');
  }

  const match = (text: string) => text.toLowerCase().includes(query.toLowerCase());
  const visiblePriority = priority.filter((r) =>
    match(`${r.subject} ${r.sender_name} ${r.sender_email}`),
  );
  const visibleHistory = history.filter(
    (r) =>
      (filter === 'all' || r.direction === filter) &&
      match(`${r.subject} ${r.sender_name} ${r.recipient_name}`),
  );
  const sentCount = history.filter((r) => r.direction === 'sent').length;
  const receivedCount = history.filter((r) => r.direction === 'received').length;
  const emailMode =
    deliveryPlan?.delivery_channel === 'email' || result?.delivery_channel === 'email';

  if (loading)
    return (
      <div className="loading-screen">
        <Logo />
        <LoaderCircle className="spin" size={24} />
        <p>Making room for what matters…</p>
      </div>
    );

  if (!user)
    return (
      <div className="welcome-page">
        <div className="welcome-nav">
          <Logo />
          <span className="eyebrow">A LITTLE LESS INBOX NOISE</span>
        </div>
        <div className="welcome-content">
          <div className="welcome-art">
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <div className="floating-note note-one">
              <span className="tiny-dot" />A gentle reminder
            </div>
            <div className="hero-envelope">
              <Mail size={100} strokeWidth={1} />
              <span className="hero-bell">
                <Bell size={30} />
              </span>
            </div>
            <div className="floating-note note-two">
              <CheckCheck size={16} />
              One thoughtful click.
            </div>
          </div>
          <div className="welcome-copy">
            <span className="section-kicker">
              <span className="tiny-dot" />
              THOUGHTFUL FOLLOW-UPS
            </span>
            <h1>
              A little nudge.
              <br />
              <em>A lot less noise.</em>
            </h1>
            <p>
              Bring important conversations back into focus with a notification or a short email
              reminder.
            </p>
            <div className="welcome-signin">
              <h3>{sessionExpired ? 'Your session has expired' : 'Connect your inbox'}</h3>
              <p>
                Connect Gmail to verify conversations and receive reminders. Email reminders use a
                separate send permission and your confirmation.
              </p>
              <button
                className="button primary"
                disabled={busy || !googleConfigured}
                onClick={() => perform(() => api.connectGoogle())}
              >
                {busy ? <LoaderCircle size={17} className="spin" /> : <Mail size={17} />}Connect
                Google
                <ArrowRight size={17} />
              </button>
              {!googleConfigured && (
                <p>
                  Google connection isn’t configured yet. Complete the Google Cloud setup to enable
                  sign-in.
                </p>
              )}
              {demoAvailable && (
                <>
                  <p className="demo-login-label">
                    Or try the fictional demo. No real Gmail account is used.
                  </p>
                  <button className="button primary" onClick={() => login('alex')} disabled={busy}>
                    {busy ? <LoaderCircle size={17} className="spin" /> : <Sparkles size={17} />}
                    Explore as Alex
                    <ArrowRight size={17} />
                  </button>
                  <button className="text-button" onClick={() => login('maya')} disabled={busy}>
                    Or explore as Maya, the recipient <ArrowUpRight size={14} />
                  </button>
                </>
              )}
            </div>
            {error && (
              <div className="inline-error" role="alert">
                {error}
              </div>
            )}
            <div className="privacy-line">
              <ShieldCheck size={16} />
              Your inbox stays yours. Always.
            </div>
          </div>
        </div>
        <footer className="welcome-footer">
          INBOX NUDGE <span>Less chasing. More connecting.</span>
          <span>Built for a more thoughtful inbox.</span>
        </footer>
      </div>
    );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Logo />
        <button className="workspace-select" onClick={() => setDialog({ kind: 'help' })}>
          <span className="workspace-icon">
            <Mail size={17} />
          </span>
          <span>
            My workspace<small>Personal account</small>
          </span>
          <ChevronDown size={15} />
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {(
            [
              ['overview', LayoutGrid, 'Overview'],
              ...(!user.demo ? [['gmail', Mail, 'Gmail inbox'] as const] : []),
              ['priority', Inbox, 'Priority inbox'],
              ['sent', Send, 'Sent conversations'],
              ['history', Clock3, 'Reminder history'],
            ] as const
          ).map(([key, Icon, label]) => (
            <button
              key={key}
              className={`nav-item ${view === key ? 'active' : ''}`}
              onClick={() => navigate(key)}
            >
              <Icon size={18} strokeWidth={1.7} />
              <span>{label}</span>
              {key === 'priority' && priority.length > 0 && (
                <span className="nav-count">{priority.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="quiet-card">
            <span className="quiet-icon">
              <Sparkles size={19} />
            </span>
            <h4>
              Small nudge.
              <br />
              Thoughtful impact.
            </h4>
            <p>
              One click keeps your
              <br />
              conversations moving.
            </p>
            <button onClick={() => setDialog({ kind: 'help' })}>
              How it works <ArrowUpRight size={14} />
            </button>
          </div>
          <button
            className={`nav-item ${view === 'settings' ? 'active' : ''}`}
            onClick={() => navigate('settings')}
          >
            <Settings2 size={18} />
            Preferences
          </button>
          <button className="nav-item" onClick={() => setDialog({ kind: 'help' })}>
            <CircleHelp size={18} />
            Help & getting started
          </button>
          <div className="sidebar-profile">
            <Avatar name={user.display_name} size="small-avatar" />
            <span>
              {user.display_name}
              <small>Personal workspace</small>
            </span>
            <button
              aria-label="Sign out"
              title="Sign out"
              className="icon-button"
              onClick={async () => {
                await setToken(null);
                setUser(null);
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="popup-brand">
            <Logo small />
          </div>
          <div className="breadcrumb">
            Workspace <ChevronRight size={14} />
            <strong>
              {view === 'gmail'
                ? 'Gmail inbox'
                : view === 'settings'
                  ? 'Preferences'
                  : view === 'sent'
                    ? 'Sent conversations'
                    : view === 'priority'
                      ? 'Priority inbox'
                      : view === 'history'
                        ? 'Reminder history'
                        : 'Overview'}
            </strong>
          </div>
          <div className="topbar-actions">
            <span className="connection">
              <span className="tiny-dot" />
              {user.demo ? 'Demo account connected' : 'Google account connected'}
            </span>
            <span className="topbar-divider" />
            <button
              className="icon-button"
              aria-label="View received reminders"
              onClick={() => {
                setFilter('received');
                navigate('history');
              }}
            >
              <Bell size={19} />
              <span className="notification-dot" />
            </button>
            <Avatar name={user.display_name} size="small-avatar" />
          </div>
        </header>
        <main>
          <div className="demo-strip">
            <span>
              <Sparkles size={14} />
              <strong>{user.demo ? 'Demo workspace' : 'Gmail connected'}</strong>
              <span className="demo-strip-detail">
                {user.demo
                  ? 'Fictional conversations. No emails or real notifications are sent.'
                  : 'Verified conversations. Each reminder shows its delivery method.'}
              </span>
            </span>
            {user.demo && (
              <button disabled={busy} onClick={() => login(user.id === 'alex' ? 'maya' : 'alex')}>
                Switch to {user.id === 'alex' ? 'Maya' : 'Alex'}
                <ArrowRight size={13} />
              </button>
            )}
          </div>
          <section className="page-heading">
            <div>
              <div className="greeting">YOUR INBOX, WITH INTENTION</div>
              <h1>{views[view][0]}</h1>
              <p>{views[view][1]}</p>
            </div>
            <div className="heading-actions">
              {isExtension && (
                <button className="button secondary expand-button" onClick={openWorkspace}>
                  <ExternalLink size={16} />
                  Open workspace
                </button>
              )}
              <button className="button primary" onClick={() => navigate('sent')}>
                <Plus size={17} />
                Send a nudge
              </button>
            </div>
          </section>
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError('')}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {view === 'gmail' && !user.demo && (
            <GmailInbox
              key={user.id}
              user={user}
              priority={priority}
              onSessionExpired={endSession}
            />
          )}
          {(view === 'overview' || view === 'priority') && (
            <>
              {view === 'overview' && (
                <>
                  <section className="hero-card">
                    <div className="hero-copy">
                      <span className="hero-kicker">
                        <span className="tiny-dot" />
                        LESS NOISE. MORE CONNECTION.
                      </span>
                      <h2>
                        Important things deserve
                        <br />a gentle reminder.
                      </h2>
                      <p>
                        Keep conversations moving, without filling inboxes.
                        <br />A thoughtful nudge is all it takes.
                      </p>
                      <button onClick={() => setDialog({ kind: 'help' })}>
                        Meet your quieter inbox <ArrowRight size={16} />
                      </button>
                    </div>
                    <div className="hero-illustration" aria-hidden="true">
                      <div className="hero-orbit" />
                      <span className="spark spark-one">✧</span>
                      <span className="spark spark-two">✦</span>
                      <div className="mail-back" />
                      <div className="illustrated-letter">
                        <span />
                        <span />
                        <span />
                        <div>
                          <Check size={18} />
                        </div>
                      </div>
                      <div className="mail-front">
                        <span />
                      </div>
                      <div className="illustrated-bell">
                        <Bell size={24} strokeWidth={1.6} />
                        <span />
                      </div>
                      <div className="illustrated-chip">
                        <span className="tiny-dot" />
                        Just a little nudge
                      </div>
                      <div className="tiny-circle" />
                    </div>
                  </section>
                  <section className="stats-grid">
                    <button className="stat-card" onClick={() => navigate('priority')}>
                      <span className="stat-icon mint">
                        <Inbox size={20} />
                      </span>
                      <span className="stat-label">
                        Needs your attention
                        <CircleHelp size={13} />
                        <strong>
                          {priority.length}
                          <small>conversations</small>
                        </strong>
                        <span className="stat-foot">
                          <span className="tiny-dot" />A reply could go a long way
                        </span>
                      </span>
                    </button>
                    <button
                      className="stat-card"
                      onClick={() => {
                        setFilter('sent');
                        navigate('history');
                      }}
                    >
                      <span className="stat-icon lilac">
                        <ArrowUpRight size={21} />
                      </span>
                      <span className="stat-label">
                        Nudges sent
                        <strong>
                          {sentCount}
                          <small>reminders</small>
                        </strong>
                        <span className="stat-foot">
                          Notifications or confirmed email reminders
                        </span>
                      </span>
                    </button>
                    <button
                      className="stat-card"
                      onClick={() => {
                        setFilter('received');
                        navigate('history');
                      }}
                    >
                      <span className="stat-icon apricot">
                        <ArrowDownLeft size={21} />
                      </span>
                      <span className="stat-label">
                        Nudges received
                        <strong>
                          {receivedCount}
                          <small>reminders</small>
                        </strong>
                        <span className="stat-foot">Your conversations, back in focus</span>
                      </span>
                    </button>
                  </section>
                </>
              )}
              <section className="panel priority-panel">
                <div className="panel-heading">
                  <div className="panel-title">
                    <h2>
                      Your priority inbox <span className="count-pill">{priority.length}</span>
                    </h2>
                    <p>Someone’s gently bringing these conversations back to your attention.</p>
                  </div>
                  <button
                    className="text-button subtle"
                    disabled={busy}
                    onClick={() => perform(async () => {}, 'Your inbox is up to date.')}
                  >
                    <RefreshCw size={14} />
                    Refresh
                  </button>
                </div>
                <div className="panel-toolbar">
                  <span className="tab active">
                    <span className="tiny-dot" />
                    Needs attention <span>{priority.length}</span>
                  </span>
                  <label className="search-box">
                    <Search size={15} />
                    <input
                      aria-label="Search conversations"
                      placeholder="Search conversations"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                    <span>⌕</span>
                  </label>
                </div>
                <div className="table-header">
                  <span>CONVERSATION</span>
                  <span>REMINDER</span>
                  <span />
                </div>
                {visiblePriority.length ? (
                  visiblePriority.map((reminder) => (
                    <div className="conversation-row" key={reminder.id}>
                      <button
                        className="conversation-main"
                        onClick={() => setDialog({ kind: 'reminder', reminder })}
                      >
                        <Avatar name={reminder.sender_name} />
                        <div>
                          <div className="sender-line">
                            <strong>{reminder.sender_name}</strong>
                            <span className="reminded-tag">
                              <Bell size={10} />
                              Nudged you
                            </span>
                          </div>
                          <h3>{reminder.subject}</h3>
                          <p>{reminder.preview}</p>
                        </div>
                      </button>
                      <span className="row-time">
                        <Clock3 size={13} />
                        {relativeTime(reminder.created_at)}
                      </span>
                      <div className="row-actions">
                        <button
                          className="open-button"
                          onClick={() => setDialog({ kind: 'reminder', reminder })}
                        >
                          View thread
                          <ArrowUpRight size={14} />
                        </button>
                        <button
                          className="icon-button row-dismiss"
                          aria-label={`Dismiss ${reminder.subject}`}
                          title="Dismiss reminder"
                          disabled={busy}
                          onClick={() =>
                            perform(
                              () => api.dismiss(reminder.id),
                              'Reminder dismissed. A little more breathing room.',
                            )
                          }
                        >
                          <Check size={17} />
                        </button>
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty
                    title={query ? 'No matching conversations' : 'A little breathing room.'}
                    text={
                      query
                        ? 'Try a different name or subject.'
                        : prefs?.reminders_enabled
                          ? user.demo
                            ? 'You’re all caught up. New reminders will appear here.'
                            : 'No nudged conversations right now. Your regular email is in Gmail inbox.'
                          : 'Reminders are paused. Turn them on in Preferences when you’re ready.'
                    }
                  />
                )}
                <div className="panel-footer">
                  <span>
                    <ShieldCheck size={14} />
                    Original conversations, brought back into focus.
                  </span>
                  <span>Sorted by latest nudge</span>
                </div>
              </section>
              {view === 'overview' && (
                <div className="bottom-grid">
                  <section className="gentle-note">
                    <span className="gentle-icon">
                      <ShieldCheck size={22} strokeWidth={1.6} />
                    </span>
                    <div>
                      <h3>Thoughtful by design.</h3>
                      <p>
                        One nudge per conversation every 24 hours.
                        <br />
                        Recipients stay in control, always.
                      </p>
                      <button className="text-button" onClick={() => navigate('settings')}>
                        Your reminder preferences
                        <ArrowRight size={14} />
                      </button>
                    </div>
                    <span className="note-decoration" aria-hidden="true">
                      ✳
                    </span>
                  </section>
                  <section className="limits-note">
                    <div>
                      <span className="section-kicker">A LITTLE GOES A LONG WAY</span>
                      <h3>
                        Your daily nudges{' '}
                        <span>
                          {user.daily_used}
                          <small> / {user.daily_limit}</small>
                        </span>
                      </h3>
                      <div className="limit-bars">
                        {Array.from({ length: user.daily_limit }, (_, i) => (
                          <span className={i < user.daily_used ? 'used' : ''} key={i} />
                        ))}
                      </div>
                      <p>Make each reminder a thoughtful one.</p>
                    </div>
                    <span className="limit-icon">
                      <Send size={23} strokeWidth={1.4} />
                    </span>
                  </section>
                </div>
              )}
            </>
          )}
          {view === 'sent' && (
            <section className="panel">
              <div className="panel-heading">
                <div className="panel-title">
                  <h2>
                    Sent conversations <span className="count-pill">{threads.length}</span>
                  </h2>
                  <p>Check eligibility before sending a gentle reminder.</p>
                </div>
                {!user.demo && (
                  <div className="sync-actions">
                    <button className="button secondary" disabled={busy} onClick={() => syncSent()}>
                      {busy ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
                      Sync recent
                    </button>
                    {nextPage && (
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => syncSent(nextPage)}
                      >
                        Load older
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="info-banner">
                <ShieldCheck size={18} />
                <span>
                  Recipients with notifications enabled get an Inbox Nudge alert. Otherwise, you can
                  confirm a short email reminder in the original conversation.
                </span>
              </div>
              <div className="sent-list">
                {threads.length ? (
                  threads.map((thread) => (
                    <div className="sent-row" key={thread.id}>
                      <span className="stat-icon mint">
                        <Send size={18} />
                      </span>
                      <div className="sent-content">
                        <span className="sent-to">To: {thread.recipient_email}</span>
                        <h3>{thread.subject}</h3>
                        <p>{thread.preview}</p>
                        <span className="sent-date">
                          Sent {relativeTime(thread.last_message_at)}
                          {thread.has_reply && ' · Reply received'}
                        </span>
                      </div>
                      <button
                        className="button secondary"
                        disabled={busy || thread.has_reply}
                        onClick={() => confirmThread(thread)}
                      >
                        {thread.has_reply ? <CheckCheck size={16} /> : <Bell size={16} />}
                        {thread.has_reply ? 'Already replied' : 'Remind recipient'}
                      </button>
                    </div>
                  ))
                ) : (
                  <Empty
                    title="No sent conversations yet"
                    text={
                      user.demo
                        ? 'Verified, unanswered conversations will appear here.'
                        : 'Choose Sync recent to import sent conversations from Gmail. Only message metadata is stored.'
                    }
                  />
                )}
              </div>
            </section>
          )}
          {view === 'history' && (
            <section className="panel">
              <div className="panel-heading">
                <div className="panel-title">
                  <h2>Reminder history</h2>
                  <p>
                    Delivery status reflects what actually happened. Showing the latest 100
                    reminders.
                  </p>
                </div>
              </div>
              <div className="panel-toolbar">
                <div className="tabs">
                  {(['all', 'sent', 'received'] as const).map((item) => (
                    <button
                      key={item}
                      className={`tab ${filter === item ? 'active' : ''}`}
                      onClick={() => setFilter(item)}
                    >
                      {item === 'all' ? 'All reminders' : item === 'sent' ? 'Sent' : 'Received'}
                    </button>
                  ))}
                </div>
                <label className="search-box">
                  <Search size={15} />
                  <input
                    aria-label="Search reminder history"
                    placeholder="Search reminders"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
              </div>
              {visibleHistory.length ? (
                visibleHistory.map((reminder) => (
                  <button
                    className="history-row"
                    key={reminder.id}
                    onClick={() => setDialog({ kind: 'reminder', reminder })}
                  >
                    <span
                      className={`stat-icon ${reminder.direction === 'sent' ? 'lilac' : 'apricot'}`}
                    >
                      {reminder.direction === 'sent' ? (
                        <ArrowUpRight size={19} />
                      ) : (
                        <ArrowDownLeft size={19} />
                      )}
                    </span>
                    <div>
                      <span className="sent-to">
                        {reminder.direction === 'sent'
                          ? `To ${reminder.recipient_name}`
                          : `From ${reminder.sender_name}`}
                      </span>
                      <h3>{reminder.subject}</h3>
                    </div>
                    <StatusPill status={reminder.status} simulated={reminder.simulated} />
                    <span className="history-time">{relativeTime(reminder.created_at)}</span>
                    <ChevronRight size={16} />
                  </button>
                ))
              ) : (
                <Empty
                  title="Nothing here just yet"
                  text="Your reminder activity will appear here, including requests that couldn’t be delivered."
                />
              )}
            </section>
          )}
          {view === 'settings' && prefs && (
            <div className="settings-grid">
              <section className="panel settings-panel">
                <div className="panel-heading">
                  <div className="panel-title">
                    <h2>Reminder preferences</h2>
                    <p>Keep the reminders you want. Pause the ones you don’t.</p>
                  </div>
                </div>
                <div className="preference-row">
                  <span className="stat-icon mint">
                    <Bell size={20} />
                  </span>
                  <div>
                    <h3>Allow incoming reminders</h3>
                    <p>Let connected senders nudge your unanswered conversations.</p>
                  </div>
                  <button
                    role="switch"
                    aria-label="Allow incoming reminders"
                    aria-checked={prefs.reminders_enabled}
                    disabled={busy}
                    className={`toggle ${prefs.reminders_enabled ? 'on' : ''}`}
                    onClick={() =>
                      perform(
                        () =>
                          api.savePreferences({
                            ...prefs,
                            reminders_enabled: !prefs.reminders_enabled,
                          }),
                        'Reminder preferences saved.',
                      )
                    }
                  >
                    <span />
                  </button>
                </div>
                <div className="preference-row">
                  <span className="stat-icon lilac">
                    <Sparkles size={20} />
                  </span>
                  <div>
                    <h3>Reminder notifications and banners</h3>
                    <p>Allow browser alerts and banners on matching Gmail conversations.</p>
                  </div>
                  <button
                    role="switch"
                    aria-label="Show reminder banners"
                    aria-checked={prefs.notification_enabled}
                    disabled={busy}
                    className={`toggle ${prefs.notification_enabled ? 'on' : ''}`}
                    onClick={() =>
                      perform(
                        () =>
                          api.savePreferences({
                            ...prefs,
                            notification_enabled: !prefs.notification_enabled,
                          }),
                        'Notification preferences saved.',
                      )
                    }
                  >
                    <span />
                  </button>
                </div>
                <div className="blocked-section">
                  <h3>Blocked senders</h3>
                  <p>These people won’t be able to send you reminders.</p>
                  <form
                    className="block-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const email = blockedEmail.trim().toLowerCase();
                      if (email)
                        perform(async () => {
                          await api.savePreferences({
                            ...prefs,
                            blocked_senders: [...new Set([...prefs.blocked_senders, email])],
                          });
                          setBlockedEmail('');
                        }, 'Sender blocked.');
                    }}
                  >
                    <input
                      aria-label="Sender email to block"
                      type="email"
                      required
                      placeholder="name@example.com"
                      value={blockedEmail}
                      onChange={(e) => setBlockedEmail(e.target.value)}
                    />
                    <button className="button secondary" disabled={busy}>
                      Block sender
                    </button>
                  </form>
                  {prefs.blocked_senders.length ? (
                    prefs.blocked_senders.map((email) => (
                      <div className="blocked-row" key={email}>
                        <BellOff size={15} />
                        <span>{email}</span>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() =>
                            perform(
                              () =>
                                api.savePreferences({
                                  ...prefs,
                                  blocked_senders: prefs.blocked_senders.filter((x) => x !== email),
                                }),
                              'Sender unblocked.',
                            )
                          }
                        >
                          Unblock
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className="no-blocked">
                      <ShieldCheck size={16} />
                      No blocked senders. You’re in control.
                    </div>
                  )}
                </div>
              </section>
              <section className="panel account-panel">
                <span className="stat-icon mint">
                  <Mail size={22} />
                </span>
                <h2>Your connection</h2>
                <Avatar name={user.display_name} />
                <h3>{user.display_name}</h3>
                <p>{user.email}</p>
                <span className="status-pill success">
                  <span className="status-dot" />
                  {user.demo ? 'Demo account connected' : 'Google account connected'}
                </span>
                <div className="account-detail">
                  <span>Daily allowance</span>
                  <strong>{user.daily_limit} requests</strong>
                </div>
                <div className="account-detail">
                  <span>Conversation cooldown</span>
                  <strong>{user.cooldown_hours} hours</strong>
                </div>
                <p className="account-note">
                  {user.demo
                    ? 'This is a fictional demo account. Connect Google from the sign-in screen to use real Gmail.'
                    : 'Only metadata is stored. Incoming reminders require your connection and permission.'}
                </p>
              </section>
              {!user.demo && <NotificationPreferences key={user.id} />}
              {!user.demo && (
                <section className="panel google-connection-panel">
                  <div className="blocked-section">
                    <h3>Google connection</h3>
                    <p>
                      {user.email} ·{' '}
                      {user.gmail_send_enabled
                        ? 'Gmail read and send access'
                        : 'Gmail read-only access'}
                    </p>
                    <p>
                      Disconnecting removes stored Google credentials and pauses incoming reminders.
                    </p>
                    <div className="connection-buttons">
                      {!user.gmail_send_enabled && (
                        <button
                          className="button primary"
                          disabled={busy}
                          onClick={() => perform(() => api.connectGoogle(true))}
                        >
                          Enable email reminders
                        </button>
                      )}
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() => perform(() => api.connectGoogle())}
                      >
                        Reconnect Google
                      </button>
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={disconnectGoogle}
                      >
                        Disconnect Google
                      </button>
                    </div>
                  </div>
                </section>
              )}
            </div>
          )}
          <footer className="main-footer">
            <span>
              <span className="tiny-logo">
                <Mail size={13} />
              </span>
              A little nudge. A more thoughtful inbox.
            </span>
            <button onClick={() => setDialog({ kind: 'help' })}>
              Made for conversations that matter <span>↗</span>
            </button>
          </footer>
        </main>
      </div>
      {notice && (
        <div className="toast" role="status">
          <CheckCheck size={18} />
          {notice}
          <button aria-label="Dismiss notification" onClick={() => setNotice('')}>
            <X size={15} />
          </button>
        </div>
      )}
      {dialog && (
        <Modal
          onClose={closeDialog}
          title={
            dialog.kind === 'thread'
              ? 'Confirm reminder'
              : dialog.kind === 'help'
                ? 'How Inbox Nudge works'
                : 'Conversation details'
          }
        >
          {dialog.kind === 'help' ? (
            <>
              <span className="modal-illustration">
                <Mail size={30} />
                <Bell size={17} />
              </span>
              <span className="section-kicker">A MORE THOUGHTFUL INBOX</span>
              <h2>A thoughtful reminder, one click away.</h2>
              <p>
                Inbox Nudge brings an existing conversation back into focus inside an
                extension-managed priority inbox.
              </p>
              <div className="help-step">
                <span>01</span>
                <div>
                  <h3>Find an unanswered conversation</h3>
                  <p>Open Sent conversations and choose a thread to nudge.</p>
                </div>
              </div>
              <div className="help-step">
                <span>02</span>
                <div>
                  <h3>Send a thoughtful reminder</h3>
                  <p>We check recipient preferences, replies, and the 24-hour cooldown first.</p>
                </div>
              </div>
              <div className="help-step">
                <span>03</span>
                <div>
                  <h3>Let them take it from there</h3>
                  <p>
                    Notification-ready recipients get an Inbox Nudge alert. Other recipients can
                    receive a short, confirmed email reminder.
                  </p>
                </div>
              </div>
              <div className="info-banner">
                <CircleHelp size={18} />
                <span>
                  Your confirmation always shows the delivery method. Recipient controls and
                  cooldowns apply to both routes. Gmail’s native ordering stays unchanged.
                </span>
              </div>
              <button className="button primary full-width" onClick={closeDialog}>
                A little less noise. Got it.
                <Check size={17} />
              </button>
            </>
          ) : dialog.kind === 'thread' ? (
            <>
              <span className="modal-illustration">
                <Bell size={29} />
              </span>
              <span className="section-kicker">A GENTLE FOLLOW-UP</span>
              <h2>{result ? statusLabels[result.status] : 'A little nudge?'}</h2>
              <p>
                {result
                  ? statusDetails[result.status]
                  : emailMode
                    ? 'Send a short email reminder in this conversation after reviewing it below.'
                    : 'Bring this conversation back to their attention inside Inbox Nudge.'}
              </p>
              <div className="thread-summary">
                <span>TO</span>
                <strong>{dialog.thread.recipient_email}</strong>
                <span>ORIGINAL CONVERSATION</span>
                <h3>{dialog.thread.subject}</h3>
              </div>
              {emailMode && deliveryPlan?.email_preview && !result && (
                <div className="email-preview">
                  <span className="section-kicker">EMAIL PREVIEW · FROM YOUR GMAIL ACCOUNT</span>
                  <p>{deliveryPlan.email_preview}</p>
                  <small>
                    A sender-specific unsubscribe link will be included. This creates a new email in
                    the original conversation.
                  </small>
                </div>
              )}
              {result?.notification_status &&
                !['none', 'queued'].includes(result.notification_status) && (
                  <p className="delivery-note" role="status">
                    {
                      (
                        {
                          push_accepted:
                            'Accepted by the browser notification service. Display depends on the recipient’s browser and device settings.',
                          no_subscription:
                            'Available in Inbox Nudge. The recipient has no active browser notification subscription.',
                          suppressed:
                            'Available in Inbox Nudge. Browser alerts are disabled by the recipient.',
                          failed:
                            'Available in Inbox Nudge. The browser notification could not be sent.',
                          unknown:
                            'Available in Inbox Nudge. Browser notification status is unknown.',
                          sending: 'Submitting the browser notification…',
                          extension_pending:
                            'The recipient’s Chrome extension will check for the alert while Chrome is running.',
                          extension_notified:
                            'Chrome accepted the alert. Display depends on the recipient’s browser and device settings.',
                        } as Record<string, string>
                      )[result.notification_status]
                    }
                  </p>
                )}
              {checking ? (
                <div className="eligibility checking">
                  <LoaderCircle className="spin" size={18} />
                  Checking reminder eligibility…
                </div>
              ) : (
                <div className={`eligibility ${eligible === 'delivered' ? 'eligible' : ''}`}>
                  <ShieldCheck size={20} />
                  <div>
                    <strong>
                      {result
                        ? statusLabels[result.status]
                        : eligible === 'delivered'
                          ? 'Eligible for a reminder'
                          : statusLabels[eligible ?? 'unable_to_verify']}
                    </strong>
                    <p>
                      {result
                        ? 'Recorded in your reminder history.'
                        : statusDetails[eligible ?? 'unable_to_verify']}
                    </p>
                  </div>
                </div>
              )}
              {user.demo && (
                <div className="simulation-note">
                  <Sparkles size={14} />
                  Demo only. No live recipient is contacted.
                </div>
              )}
              {user.demo && result?.status === 'recipient_not_connected' && (
                <div className="gmail-simulation">
                  <span>DEMO DELIVERY EXPLANATION</span>
                  <div>
                    <span className="gmail-mark">M</span>
                    <div>
                      <strong>A real account can offer an email reminder</strong>
                      <p>{dialog.thread.subject}</p>
                    </div>
                  </div>
                  <small>
                    Requires separate Gmail send permission and your confirmation. The demo sends
                    nothing.
                  </small>
                </div>
              )}
              <div className="modal-actions">
                {result ? (
                  <>
                    {result.status === 'email_unknown' && (
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() =>
                          perform(async () => {
                            setResult(await api.reconcile(result.id));
                          })
                        }
                      >
                        Check Gmail Sent
                      </button>
                    )}
                    <button className="button primary full-width" onClick={closeDialog}>
                      Done
                      <Check size={16} />
                    </button>
                  </>
                ) : (
                  <>
                    <button className="button secondary" disabled={busy} onClick={closeDialog}>
                      Maybe later
                    </button>
                    {eligible === 'email_permission_required' ? (
                      <button
                        className="button primary"
                        disabled={busy}
                        onClick={() => perform(() => api.connectGoogle(true))}
                      >
                        Enable email reminders
                      </button>
                    ) : (
                      <button
                        className="button primary"
                        disabled={
                          busy ||
                          checking ||
                          !['delivered', 'recipient_not_connected', 'email_ready'].includes(
                            eligible ?? '',
                          )
                        }
                        onClick={() =>
                          perform(async () => {
                            setResult(
                              await api.remind(
                                dialog.thread.id,
                                idempotency.current,
                                emailMode ? 'email' : 'in_app',
                              ),
                            );
                          })
                        }
                      >
                        {busy ? <LoaderCircle className="spin" size={16} /> : <Bell size={16} />}
                        {eligible === 'email_ready'
                          ? 'Send email reminder'
                          : eligible === 'recipient_not_connected'
                            ? 'Record request'
                            : 'Send reminder'}
                      </button>
                    )}
                  </>
                )}
              </div>
            </>
          ) : (
            <>
              <div className="thread-modal-top">
                <Avatar name={dialog.reminder.sender_name} />
                <div>
                  <strong>{dialog.reminder.sender_name}</strong>
                  <p>{dialog.reminder.sender_email}</p>
                </div>
                <StatusPill status={dialog.reminder.status} simulated={dialog.reminder.simulated} />
              </div>
              <h2>{dialog.reminder.subject}</h2>
              <div className="message-meta">
                To {dialog.reminder.recipient_email}
                <span>{relativeTime(dialog.reminder.created_at)}</span>
              </div>
              <div className="message-body">
                {dialog.reminder.preview ||
                  'Email content stays in Gmail. Open the original conversation to read or reply.'}
                {!dialog.reminder.simulated && dialog.reminder.provider_thread_id && (
                  <a
                    className="button secondary gmail-thread-link"
                    target="_blank"
                    rel="noreferrer"
                    href={gmailLink(dialog.reminder.provider_thread_id)}
                  >
                    Open original in Gmail
                    <ExternalLink size={14} />
                  </a>
                )}
              </div>
              <div className="eligibility eligible">
                <Bell size={19} />
                <div>
                  <strong>
                    {dialog.reminder.direction === 'received'
                      ? 'A gentle reminder from the sender'
                      : statusLabels[dialog.reminder.status]}
                  </strong>
                  <p>{statusDetails[dialog.reminder.status]}</p>
                </div>
              </div>
              {dialog.reminder.simulated && (
                <div className="simulation-note">
                  <Sparkles size={14} />
                  Fictional demo conversation. No real Gmail thread is linked.
                </div>
              )}
              <div className="modal-actions">
                {dialog.reminder.direction === 'received' && (
                  <button
                    className="text-button danger"
                    disabled={busy}
                    onClick={() =>
                      perform(async () => {
                        if (!prefs) return;
                        await api.savePreferences({
                          ...prefs,
                          blocked_senders: [
                            ...new Set([...prefs.blocked_senders, dialog.reminder.sender_email]),
                          ],
                        });
                        closeDialog();
                      }, 'Sender blocked. Their reminders are hidden from your priority view.')
                    }
                  >
                    <BellOff size={15} />
                    Block sender
                  </button>
                )}
                {dialog.reminder.direction === 'received' &&
                dialog.reminder.status === 'delivered' ? (
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() =>
                      perform(async () => {
                        await api.dismiss(dialog.reminder.id);
                        closeDialog();
                      }, 'Reminder dismissed.')
                    }
                  >
                    <Check size={16} />
                    Dismiss reminder
                  </button>
                ) : (
                  <button className="button primary" onClick={closeDialog}>
                    Done
                    <Check size={16} />
                  </button>
                )}
              </div>
            </>
          )}
        </Modal>
      )}
      <div className="mobile-nav">
        {!user.demo && (
          <button
            aria-label="Gmail inbox"
            className={view === 'gmail' ? 'active' : ''}
            onClick={() => navigate('gmail')}
          >
            <Mail size={20} />
            <span>Gmail</span>
          </button>
        )}
        <button
          aria-label="Overview"
          className={view === 'overview' ? 'active' : ''}
          onClick={() => navigate('overview')}
        >
          <LayoutGrid size={20} />
          <span>Overview</span>
        </button>
        <button
          aria-label="Priority inbox"
          className={view === 'priority' ? 'active' : ''}
          onClick={() => navigate('priority')}
        >
          <Inbox size={20} />
          <span>Priority</span>
        </button>
        <button
          aria-label="Sent conversations"
          className={view === 'sent' ? 'active' : ''}
          onClick={() => navigate('sent')}
        >
          <Send size={20} />
          <span>Send</span>
        </button>
        <button
          aria-label="Reminder history"
          className={view === 'history' ? 'active' : ''}
          onClick={() => navigate('history')}
        >
          <Clock3 size={20} />
          <span>History</span>
        </button>
        <button
          aria-label="Preferences"
          className={view === 'settings' ? 'active' : ''}
          onClick={() => navigate('settings')}
        >
          <Settings2 size={20} />
          <span>Settings</span>
        </button>
      </div>
    </div>
  );
}
