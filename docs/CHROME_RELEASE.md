# Publish Inbox Nudge on the Chrome Web Store

Current status: local extension only. No developer account, public HTTPS backend, public Google approval, or Store submission is configured. The extension can stay the only essential user interface; basic public product/privacy/support pages support review and account help.

## 1. Register the publisher and reserve an item ID

Open the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole) using the Google account that will own the extension. Complete developer registration and its one-time fee, account verification and security requirements. Do not share your password or payment details with a coding tool. [Official registration instructions](https://developer.chrome.com/docs/webstore/register).

Upload `dist/inbox-nudge-extension.zip` as an **unpublished draft only** to reserve an item ID. This ZIP points to localhost and must not be submitted for public review or published. Record the assigned 32-letter ID. Replace the draft package with the verified production build in step 5. Draft creation does not make the extension available to users. [Upload/submission flow](https://developer.chrome.com/docs/webstore/publish).

## 2. Host the backend over HTTPS

Use a managed container/app host with managed PostgreSQL. The repository includes a Dockerfile; it defaults to production mode and copies only runtime code. A hosting provider/domain/budget has not been selected, so no resources have been provisioned or billed.

Configure secrets using [backend/.env.production.example](../backend/.env.production.example), not in the extension or a public repository:

- `APP_ENV=production`, `DEMO_MODE=false`, `WEB_AUTH_ENABLED=false`, `REQUEST_LIMITS_ENABLED=true`.
- A unique random `JWT_SECRET` of at least 48 characters and a stable Fernet `TOKEN_ENCRYPTION_KEY`. Store them in the host’s secret manager, separate from encrypted database backups.
- PostgreSQL `DATABASE_URL` with `sslmode=verify-full` and the provider’s trusted CA (`sslrootcert` when required). Enable managed storage encryption and test backup restoration.
- Google client ID/secret and the actual HTTPS `GOOGLE_REDIRECT_URI`, normally `https://API_DOMAIN/api/auth/google/callback`.
- `EXTENSION_ID` equal to the draft Store item ID. Leave `CORS_ORIGINS` empty for the extension-only service; no website dashboard is required.

The container listens on port 8000 and applies migrations on startup. Start with one API instance for initial deployment; test PostgreSQL locking/worker behavior before scaling. The application request counters are shared through PostgreSQL. Configure the hosting edge for request/connection timeouts, body limits, volumetric abuse protection and explicit trusted proxy forwarding. Do not blindly trust incoming `X-Forwarded-For`, and do not use `--forwarded-allow-ips='*'` on a directly exposed server. Disable access logging of OAuth callback queries and private unsubscribe URLs at the proxy as well as Uvicorn.

Chrome clients call `/api/health`. The release gate expects `status=ok`, `deployment_environment=production`, `demo_mode=false`, `google_configured=true`, `request_limits_enabled=true`, and the exact public `extension_id`. Health contains configuration flags/public IDs, not account data or secrets.

## 3. Complete Google OAuth verification

Configure a production Google project/consent screen. Register the HTTPS callback exactly, prove ownership of the product domain, and supply the homepage, privacy policy, support details and a demo video explaining every requested scope.

`gmail.readonly` is **restricted**; `gmail.send` is **sensitive**. This backend accesses and stores Gmail-derived metadata, so public distribution needs the applicable restricted-scope verification and, unless an exception applies, an annual Google-approved third-party security assessment. Reading only headers does not change the scope’s classification. The automated checks in this repository do not substitute for that assessment. Development/test-user exceptions do not authorize general public rollout. [Gmail scope classifications](https://developers.google.com/workspace/gmail/api/auth/scopes), [restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

Recipients without the extension use the separately confirmed email fallback. Custom Chrome alerts require the recipient’s participating extension; do not advertise universal or guaranteed device notifications.

## 4. Finalize public disclosures and deletion/retention operations

Use the drafts in [store](store/) as source material, then fill in the actual operator identity, public URLs, support address, hosting/subprocessor details, retention periods, backup-expiration rules and verified deletion workflow. Publish accurate privacy/support/home pages. No privacy policy or deletion process has been approved or published by this task.

Disconnect currently removes Google credentials and invalidates sessions; it does **not** erase conversation/reminder history. Do not promise complete account deletion until an operator deletion procedure or product feature has been implemented and tested. Decide how suppression/blocked-sender records and shared reminders are handled before offering erasure. [Chrome’s disclosure requirements](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq).

## 5. Build the production package

Set these public settings in the root `.env` or build environment; they are not credentials:

```dotenv
NUDGE_API_ORIGIN=https://YOUR_REAL_API_DOMAIN
NUDGE_STORE_EXTENSION_ID=YOUR_32_LETTER_STORE_ITEM_ID
```

With Node 22.12+/24/26 and the project Python environment installed:

```sh
npm ci
npm run build:store
```

The command refuses localhost, IPs, reserved placeholder domains, disabled TLS verification and an unverified/mismatched backend. It creates `dist/store-extension`, a versioned Store ZIP, and `dist/store-release.json` recording the public origin, version, item ID and package SHA-256. Only generated extension assets are archived; source, environment files, databases, symlinks and unrelated files are excluded. This gate checks configuration, not legal approval or the absence of every vulnerability.

Replace the unpublished draft with that ZIP. Test the exact item ID against the hosted backend. If you test unpacked with a different ID, the backend must not be widened to arbitrary return URLs; use the Store-provided public key for a consistent development ID or a separately isolated test deployment. [Stable extension IDs](https://developer.chrome.com/docs/extensions/reference/manifest/key).

## 6. Validate and submit

Run the automated checks in README, then test two real consenting Google accounts against the production candidate: setup, sender-owned Sent conversation, connected recipient alert, recipient blocking/disable/dismissal, the email preview/cancel/confirmation route, opt-out, browser restart/reload, sign-out and disconnect. Confirm no unintended email is sent and that device acceptance is not presented as proof of reading. Complete PostgreSQL/concurrency, TLS, backup/restore, data-retention/deletion and independent security checks.

Fill out Store Listing, Privacy, Distribution and Test instructions using the drafts. Provide the 128×128 icon and at least one accurate screenshot; follow the current [image requirements](https://developer.chrome.com/docs/webstore/images). Existing screenshots under `test-results/previews` use synthetic test accounts/Gmail fixtures and are **not final Store screenshots**. Capture final product screenshots with consenting test data and label examples accurately.

Initially use an appropriate limited tester distribution. Once the actual production package, policies, Google review and independent assessment are complete, submit for Chrome review. Choose deferred/manual publication so approval does not automatically launch to everyone. No item has been uploaded, submitted or published by this task. [Official publishing instructions](https://developer.chrome.com/docs/webstore/publish).
