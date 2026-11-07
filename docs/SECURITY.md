# Security and privacy

Inbox Nudge’s security boundaries are implemented and tested locally. This is not a claim that the extension is vulnerability-free or ready for unrestricted public release.

## Extension access

The manifest requests `identity`, `storage`, `alarms`, and `notifications`. Its content script runs only on `https://mail.google.com/*`; network host access and CSP connections are restricted to the configured API origin. It requests no cookies, browser history, all-sites access, or `tabs` permission. Opening known Gmail/setup URLs and obtaining tab IDs do not require reading browsing history. The extension uses only packaged JavaScript, `script-src 'self'`, `object-src 'none'`, and a fixed API origin.

Google OAuth opens after the user clicks Connect Gmail. State, nonce, PKCE, signed identity checks, mailbox ownership, exact redirect allowlists, and a one-time handoff protect the flow. The sender sees read/send permission disclosed before consent. Read-only grants remain usable for receiving nudges and participating delivery; email sending checks the separate send grant.

Both `chrome.storage.local` and `chrome.storage.session` use `TRUSTED_CONTEXTS`. Gmail content scripts and page scripts cannot access stored credentials. The content bridge checks the owning extension, Gmail origin, top-level frame, signed-in mailbox, tab ID, expiry, and server-created confirmation ticket. It exposes no generic HTTP bridge or token operation. A forged recipient/subject from Gmail’s DOM cannot authorize an unverified conversation; the server verifies metadata from the authenticated mailbox and requires a unique match when IDs are absent.

These choices follow Chrome’s guidance on [minimal permissions and secure message handling](https://developer.chrome.com/docs/extensions/develop/security-privacy/stay-secure) and [restricted storage access](https://developer.chrome.com/docs/extensions/reference/api/storage).

## Credentials and sessions

Google credentials are encrypted with a stable private Fernet key on the backend. They are decrypted only for provider requests. Google client secrets, encryption keys, and private notification keys are absent from the extension bundle.

The extension stores a short-lived application access token in restricted session storage and an opaque application refresh credential in restricted local storage. The server stores only its SHA-256 hash. A rotation family expires after thirty days; rotation never extends that deadline. Reusing a consumed credential revokes its entire family. Access JWTs include a family binding and are checked against active server sessions, so sign-out invalidates the profile’s existing access tokens too. Google disconnect invalidates all app sessions and removes extension registrations.

Concurrent refresh calls share one in-flight operation in the background worker. User-row/session locking prevents a concurrent logout/rotation from leaving a live successor. Account-change checks stop a late refresh from restoring credentials after sign-out. A lost refresh response may require reconnecting rather than accepting a replay. Remote sign-out failures are reported while local credentials are still cleared.

Chrome local storage is not a hardware-backed secret vault; trusted-context restriction does not protect against malware or someone who controls the local browser profile. Backend encryption also depends on keeping its key separate from database backups. Account subjects and conversation metadata are not end-to-end encrypted.

## Data and delivery

The backend stores Google identity, encrypted credentials, verified thread IDs, sender/recipient addresses, subjects, timestamps, recipient controls, hashed session credentials, device readiness, and reminder/audit results. Real message bodies and snippets are not persisted. Google’s `gmail.readonly` permission technically allows broader reads; metadata-only API calls and a fixed-host provider client limit what this implementation fetches. [Google’s scope definitions](https://developers.google.com/workspace/gmail/api/auth/scopes).

Chrome notification text is generic and omits email subjects/addresses. The extension polls only the authenticated recipient’s active reminders, respects incoming/blocked-sender controls, and deduplicates alerts. Clicking an alert reconstructs a fixed Gmail URL using the authenticated account and its mailbox-scoped verified thread ID. It never follows an arbitrary notification URL. Chrome acceptance does not establish display or reading.

Email fallback requires an explicit preview confirmation, verified sender/recipient, Gmail send permission, recipient preferences, and quota/cooldown checks. A durable irreversible-send boundary, deterministic Message-ID, and idempotency keys prevent automatic retries after ambiguous responses. Unsubscribe tokens are opaque and encrypted/hashed in storage. GET confirmation is side-effect-free; POST blocks reminders from that sender without sign-in. Confirmation pages escape user names and use a restrictive CSP, no JavaScript, and no-referrer/no-store headers.

No raw codes, credentials, provider response bodies, or private push endpoints belong in logs. Local and container commands disable API access logs. Hosted proxies must also avoid logging OAuth callback queries and private capability URLs. `.env` files and local database backups are private and excluded from version control.

## Verification and release limits

Automated tests cover OAuth identity/PKCE, mailbox isolation, metadata-only storage, rotating sessions and replay revocation, expiry/device binding, logout/refresh races, disconnect, recipient controls, quota/concurrency, unambiguous Gmail matching, confirmed email delivery, ambiguous-send handling, and opt-outs. Browser tests exercise an unpacked extension against synthetic Gmail and mocked Google/notification services, including rejection of content-script credential access and cross-tab confirmation reuse.

The release still needs real two-account Gmail/Chrome validation, PostgreSQL concurrency validation, HTTPS hosting, Google verification, Chrome Web Store review, global abuse controls, operational secret management, a published privacy policy and data retention/deletion policy, and an independent security review. These have not been completed by local automated tests. Users can currently disconnect to remove Google credentials; disconnect does not erase stored conversation/reminder history. Removing the extension alone cannot reliably notify an offline backend to delete account data.

The latest [audit report](SECURITY_AUDIT.md) records fixes and remaining limits. Request limits now use short-lived database counters shared across API instances: 30 authentication requests/minute, 45 Gmail-verification requests/minute, and 1,200 other API requests/minute per ASGI peer. Raw forwarded-address headers are not trusted. Counts use HMAC-address keys and expire through request-time cleanup; they contain no raw addresses. Bodies are limited to 128 KiB, including chunked/misdeclared bodies, and URLs to 8 KiB. These are application controls, not volumetric DDoS protection; configure trusted load-balancer forwarding, transport timeouts and edge protections at deployment.

`APP_ENV=production` refuses demo login, short/default signing secrets, missing Google/Store configuration, disabled request limits, non-public/non-HTTPS OAuth callbacks, and PostgreSQL connections lacking `sslmode=verify-full`. The container defaults to production; local Compose explicitly uses development. Docker copies only runtime source/migrations/configuration and excludes environment files, database backups and credential downloads. JWT secrets are masked and database URLs excluded from configuration representations/serialization. Validation failures no longer echo credential-bearing inputs. Optional website-push requests do not follow redirects.

Credential writes are serialized with logout and account changes. Private API responses are rejected after the connection changes, Gmail workflow replies retain their initial connection binding, and popup mutations require the account displayed in that popup. Regression tests reproduce the earlier race conditions rather than assuming a generation check around asynchronous storage was sufficient.
