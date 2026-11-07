# Inbox Nudge

A Chrome extension that adds **Nudge** to unanswered conversations in Gmail’s Sent view. Install it, connect Gmail in the extension’s setup screen, and stay in Gmail. The popup contains connection status, settings, and recent delivery results. The optional web dashboard is not part of the normal workflow.

Connected recipients can receive Chrome notifications without visiting a website or registering for website push. Other recipients can receive a short, explicitly confirmed email reminder. Custom notifications cannot reach an arbitrary address that has no participating extension. Chrome and device settings affect notification display.

## Local development

Requires Node 22.12+ (or a supported Node 24/26 release), Python 3.12+, and Chrome 120+.

```sh
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install -e ./backend --no-deps
cp backend/.env.example backend/.env
```

Edit an existing `.env` instead of overwriting it. Generate a unique `JWT_SECRET` and a stable `TOKEN_ENCRYPTION_KEY`. The existing workspace already has private secrets. Configure your Google client using [Gmail setup](docs/GMAIL_SETUP.md).

Start the backend from `backend/`:

```sh
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

From the project root:

```sh
npm run build
```

1. In `chrome://extensions`, enable Developer mode and **Load unpacked → dist/extension**.
2. Set `EXTENSION_ID` in `backend/.env` to the extension’s displayed ID and restart the API. These are developer setup steps; Store users will not configure servers or credentials.
3. The extension opens its own setup tab on first installation. Click **Connect Gmail** and approve Google’s permissions. No separate Inbox Nudge account is required.
4. Click **Open Gmail**, open an unanswered single-recipient conversation in **Sent**, then click **Nudge**. Existing Gmail tabs may need a reload after installing or updating the extension.
5. Review the verified recipient and delivery method in Gmail. Email delivery always shows a preview and requires **Send email reminder**. No message is sent by merely opening the dialog.

The sender’s Google connection requests read-only Gmail access for verification and send access for the disclosed email fallback in one consent flow. Send permission is not required to receive Chrome nudges. If it is declined, the extension can still offer connected-recipient delivery; reconnect to enable email later. No modify/delete Gmail permission is requested.

## Everyday behavior

- **Sender:** the Gmail Nudge button verifies a conversation using a mailbox-scoped ID. If Gmail hides that ID, a bounded metadata search must find exactly one provider-verified match; ambiguous conversations are rejected. No dashboard syncing is required.
- **Recipient with extension:** connect Gmail once, keep incoming nudges/Chrome alerts enabled, and receive a generic Chrome notification. Clicking it opens the original conversation in the correct Gmail account. Gmail also shows a reminder banner with **Dismiss** and marks matching inbox rows.
- **Recipient without extension:** the sender can confirm a short email reminder. Recipients need no signup to receive it or stop future reminders from that sender. Unsubscribe confirmation is served directly by the API, without the dashboard or JavaScript.
- **Settings:** the extension icon opens a compact popup with incoming nudges, Chrome notifications, blocked senders, recent results, sign-out, and Google disconnect.

Chrome checks for nudges approximately once a minute while running; this is polling, not an instant delivery guarantee. Browser alarms may be delayed. Alert text omits the email subject and addresses. A Chrome-accepted notification does not prove it was displayed or read. A stale extension registration stops being notification-ready after seven days without contact.

The extension reconnects its short-lived application session automatically across browser restarts using a rotating application refresh credential. Sessions have an absolute thirty-day lifetime and may require Google sign-in sooner after revocation. Google’s Testing-mode refresh tokens can also expire earlier. One account is connected per Chrome profile; use separate profiles for different Gmail accounts.

## Security and data handling

For the latest review and public-release blockers, see [Security audit](docs/SECURITY_AUDIT.md) and [Chrome Web Store release steps](docs/CHROME_RELEASE.md). `npm run build:store` checks a real HTTPS production backend and the assigned Store item ID before producing an upload package. The ordinary local-development ZIP is not a public-release build.

See [Security and privacy](docs/SECURITY.md) for the implemented boundaries, tests, and remaining release work.

Google access/refresh tokens are encrypted on the backend and never enter Gmail or the extension. A hashed, rotating application refresh credential supports persistent sign-in. Both Chrome storage areas are restricted to trusted extension contexts. Gmail content scripts can invoke only specific account/tab-bound actions; they cannot get tokens or issue arbitrary API requests. Packaged scripts and a restrictive CSP prevent remote code loading.

The app fetches Gmail headers/metadata, not message bodies. The database retains account identity, connection credentials, conversation metadata, preferences, and reminder/audit records. A broad Google read-only scope is required for Gmail search; the implementation uses metadata-only requests. This is not end-to-end encryption of stored conversation metadata.

**Sign out** revokes this profile’s application session and clears local credentials. **Disconnect Google** additionally revokes provider access where possible, erases stored Google tokens, invalidates all app sessions, removes extension registrations, and pauses incoming nudges. If remote revocation cannot be confirmed, remove access in Google Account → Third-party connections.

Every recorded request counts toward a rolling ten-request/day allowance. Delivered, queued, sent, and ambiguous reminders have a 24-hour conversation cooldown. Idempotent retries return the same record. The backend rechecks conversation ownership, replies, recipient controls, permissions, and cooldowns before sending an email. A timeout or interrupted send becomes **Email send status unknown**, is never automatically retried, and can be reconciled against Gmail Sent from the popup’s recent results.

## Backend and deployment

The extension needs a shared backend service; users do not need a website dashboard. The local build points to `127.0.0.1:8000`, which works on this computer only. Before distributing the extension, host the API over HTTPS and build with a root `.env` containing:

```dotenv
NUDGE_API_ORIGIN=https://api.your-domain.example
```

Configure the actual Google OAuth callback URL, published extension ID, database, secrets, and CORS origins. Use PostgreSQL for hosting. No deployment or Chrome Web Store publication has been performed. Public distribution still needs the applicable Google OAuth review, Store review, privacy disclosures, retention/deletion policy, and independent security testing.

```sh
docker compose up --build
```

Compose reads configuration from the root `.env` or shell, not `backend/.env`. It starts PostgreSQL and the API; replace demonstration credentials and disable `DEMO_MODE` for real hosted use. Runtime Docker validation requires a running daemon.

Startup applies Alembic migrations without deleting existing accounts or reminders. Explicit migration:

```sh
cd backend
../.venv/bin/python -m app.migrate
```

The optional website remains available via `npm run dev` at `http://127.0.0.1:5173` for development/demo use. Website push is an optional separate channel; extension notifications need no VAPID keys, service-worker subscription, or website visit.

## Verification

```sh
npm run build
npm run lint
npm run format:check
npm test
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python -m pytest backend/tests -q
npm run test:e2e
```

Backend tests use temporary databases and real migrations; Google and push HTTP are mocked. Browser tests start their own API/UI on ports 8011/5174 and build `dist/e2e-extension`, leaving the regular app/database untouched. The Chrome test loads the real extension in an isolated profile, renders a synthetic Gmail page, exercises the Nudge dialog and email confirmation, checks credential isolation and tab binding, renews a session, and mocks notification delivery. Tests do not send real mail or desktop alerts. Current Gmail DOM compatibility and actual two-account delivery still require live validation.

The main implementation is `extension/content.ts` (Gmail UI), `extension/background.ts` (typed actions and Chrome alerts), `extension/session.ts` (restricted credential storage/rotation), and `src/ExtensionApp.tsx` (setup/popup). Backend session rotation lives in `extension_sessions.py`; device readiness/receipts in `extension_delivery.py`; mailbox verification in `gmail.py`; delivery jobs in `dispatch.py`.
