import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bell,
  Inbox,
  LoaderCircle,
  Mail,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-react';
import { api, ApiError } from './api';
import type { GmailConversation, Reminder, User } from './types';

export default function GmailInbox({
  user,
  priority,
  onSessionExpired,
}: {
  user: User;
  priority: Reminder[];
  onSessionExpired: () => void;
}) {
  const [threads, setThreads] = useState<GmailConversation[]>([]);
  const [nextPage, setNextPage] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [loaded, setLoaded] = useState(false);
  const active = useRef<AbortController | null>(null);
  const fetchPage = useCallback(
    async (pageToken?: string) => {
      active.current?.abort();
      const controller = new AbortController();
      active.current = controller;
      setLoading(true);
      setError('');
      try {
        const result = await api.gmailInbox(pageToken, controller.signal);
        if (controller.signal.aborted) return;
        setThreads((previous) => {
          const values = pageToken ? [...previous, ...result.threads] : result.threads;
          return [...new Map(values.map((value) => [value.provider_thread_id, value])).values()];
        });
        setNextPage(result.next_page_token ?? undefined);
        setLoaded(true);
      } catch (failure) {
        if (controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.status === 401) onSessionExpired();
        else
          setError(failure instanceof Error ? failure.message : 'Unable to load your Gmail inbox.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [onSessionExpired],
  );

  useEffect(() => {
    void fetchPage();
    return () => active.current?.abort();
  }, [fetchPage]);

  const filtered = threads.filter((thread) =>
    `${thread.subject} ${thread.sender_name} ${thread.sender_email}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const link = (id?: string) =>
    `https://mail.google.com/mail/u/?authuser=${encodeURIComponent(user.email)}${id ? `#all/${encodeURIComponent(id)}` : '#inbox'}`;

  return (
    <section className="panel gmail-inbox-panel" aria-busy={loading}>
      <div className="panel-heading">
        <div className="panel-title">
          <h2>
            <Mail size={17} />
            Gmail inbox <span className="count-pill">{threads.length}</span>
          </h2>
          <p>{user.email} · Your regular inbox conversations</p>
        </div>
        <div className="sync-actions">
          <button className="text-button subtle" disabled={loading} onClick={() => fetchPage()}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
            Refresh
          </button>
          <a className="button secondary" href={link()} target="_blank" rel="noreferrer">
            Open Gmail
            <ArrowUpRight size={14} />
          </a>
        </div>
      </div>
      <div className="panel-toolbar">
        <span className="tab active">
          Inbox <span>{threads.length} loaded</span>
        </span>
        <label className="search-box">
          <Search size={15} />
          <input
            aria-label="Search loaded Gmail conversations"
            placeholder="Search loaded mail"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {error && (
        <div className="gmail-inbox-error" role="alert">
          <p>{error}</p>
          <button className="button secondary" disabled={loading} onClick={() => fetchPage()}>
            Try again
          </button>
        </div>
      )}
      {loading && !loaded && (
        <div className="gmail-inbox-loading" role="status">
          <LoaderCircle className="spin" size={25} />
          <p>Loading your Gmail inbox…</p>
        </div>
      )}
      {filtered.map((thread) => {
        const nudged = priority.some(
          (reminder) =>
            !reminder.simulated && reminder.provider_thread_id === thread.provider_thread_id,
        );
        const timestamp = new Date(thread.last_message_at);
        return (
          <a
            key={thread.provider_thread_id}
            className={`gmail-inbox-row ${thread.unread ? 'unread' : ''}`}
            href={link(thread.provider_thread_id)}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${thread.subject} in Gmail`}
          >
            <span
              className={`gmail-read-indicator ${thread.unread ? 'unread' : ''}`}
              aria-label={thread.unread ? 'Unread' : 'Read'}
            />
            <span className="gmail-sender-avatar">
              {(thread.sender_name[0] || '?').toUpperCase()}
            </span>
            <div className="gmail-inbox-content">
              <div className="gmail-sender-line">
                <strong>{thread.sender_name}</strong>
                {thread.message_count > 1 && <span>{thread.message_count}</span>}
                {nudged && (
                  <span className="reminded-tag">
                    <Bell size={10} />
                    Nudged
                  </span>
                )}
              </div>
              <h3>{thread.subject || '(No subject)'}</h3>
              <p>{thread.sender_email}</p>
            </div>
            <time title={timestamp.toLocaleString()} dateTime={thread.last_message_at}>
              {timestamp.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </time>
            <ArrowUpRight size={15} className="gmail-open-icon" />
          </a>
        );
      })}
      {loaded && !error && !filtered.length && (
        <div className="empty">
          <span className="empty-icon">
            <Inbox size={28} />
          </span>
          <h3>{query ? 'No matching conversations' : 'Your inbox is clear.'}</h3>
          <p>
            {query
              ? 'Search matches conversations loaded in this view. Load older mail to search further.'
              : 'Gmail returned no conversations on this page.'}
          </p>
        </div>
      )}
      {nextPage && (
        <div className="gmail-load-more">
          <button
            className="button secondary"
            disabled={loading}
            onClick={() => fetchPage(nextPage)}
          >
            {loading ? <LoaderCircle className="spin" size={15} /> : <Inbox size={15} />}Load older
            conversations
          </button>
        </div>
      )}
      <div className="panel-footer">
        <span>
          <ShieldCheck size={14} />
          Read-only view · Read and reply in Gmail
        </span>
        <span>Showing loaded conversations</span>
      </div>
    </section>
  );
}
