# Configure Gmail for the Chrome extension

These are developer setup steps. A published extension’s users should only install it, connect their Gmail account, and use Nudge in Gmail. Keep Google secrets and downloaded credential JSON private.

## Google project

1. In [Google Cloud Console](https://console.cloud.google.com/), select/create Inbox Nudge and enable **Gmail API**.
2. Under **Google Auth platform → Branding**, set the app name, support email, and developer contact.
3. Under **Audience**, choose External for personal Gmail and add development accounts as test users while Testing is enabled. Workspace accounts may also require administrator approval.
4. Under **Data Access**, add `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.send`, and identity email/profile scopes. The sender’s extension setup requests read/send access together; ordinary optional web sign-in remains read-only. Google’s permission screen remains the user’s consent boundary.
5. Create an OAuth client of type **Web application** with this exact authorized callback:

```text
http://127.0.0.1:8000/api/auth/google/callback
```

The backend handles Google’s authorization-code exchange and keeps the client secret. The extension’s later Chromium handoff is verified separately; it is not Google’s callback URI. [Google server authorization](https://developers.google.com/identity/protocols/oauth2/web-server).

## Private backend configuration

In `backend/.env`:

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/auth/google/callback
WEB_APP_ORIGIN=http://127.0.0.1:5173
```

`WEB_APP_ORIGIN` is used only for the optional web login/dashboard. Extension setup and delivery do not require it to be running. Keep `JWT_SECRET` random and at least 32 characters, and preserve `TOKEN_ENCRYPTION_KEY`. Both have already been generated in this workspace. For a fresh checkout, generate from the project root and save the values locally:

```sh
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
chmod 600 backend/.env
```

Build and load:

```sh
npm run build
```

In `chrome://extensions`, enable Developer mode, choose **Load unpacked → dist/extension**, copy the displayed extension ID to `EXTENSION_ID` in `backend/.env`, and restart the API:

```sh
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

This allowlists only `https://<id>.chromiumapp.org/google` as the extension’s return destination. The client secret never belongs in the manifest. [Chrome identity API](https://developer.chrome.com/docs/extensions/reference/api/identity). Reload the extension after rebuilding; reload existing Gmail tabs after updating.

## Use it entirely in the extension

1. First installation opens the extension’s own setup tab. Click **Connect Gmail**, select the Gmail account you will use, and approve Google’s permissions. No separate account signup or website is needed.
2. Click **Open Gmail** and open an unanswered, single-recipient conversation you started in **Sent**.
3. Click **Nudge**. The Gmail dialog shows the server-verified recipient, subject, and delivery method. A connected recipient can receive a Chrome notification; email fallback shows its text and requires **Send email reminder**.
4. In another Chrome profile, load the extension and connect the recipient Gmail account to the same API. Keep **Receive nudges** and **Chrome notifications** enabled. No website notification permission or VAPID setup is needed.
5. Chrome checks approximately once per minute while running. A notification opens the correct original Gmail conversation. Banners and inbox indicators are supplied by the Gmail content script. Device settings can suppress alerts.
6. Open the extension icon for settings, sender blocking, recent outcomes, sign-out, and Google disconnect. Unknown email outcomes can be checked against Gmail Sent without resending.

Only metadata is fetched; email bodies stay in Gmail. The read-only scope is needed for Message-ID/subject search, which is unavailable with the narrower `gmail.metadata` scope. The implementation requests no Gmail modify/delete permission. [Scope definitions](https://developers.google.com/workspace/gmail/api/auth/scopes).

Chrome stores only application credentials in restricted extension storage; Google tokens stay encrypted on the server. The application session rotates automatically for up to thirty days. Google Testing-mode refresh tokens for these scopes may expire sooner, requiring reconnection. [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

## Shared service for distribution

`127.0.0.1` points to each user’s own computer. Other computers need the same hosted HTTPS API. Build with a root `.env` containing:

```dotenv
NUDGE_API_ORIGIN=https://api.your-domain.example
```

Set the backend’s actual HTTPS Google callback, published extension ID, database, secrets, and exact CORS origins. Register the callback in Google Cloud. Serve the API’s unsubscribe pages publicly; no dashboard deployment is necessary. Google consent branding/privacy pages and Store listing information can be basic public pages.

No public hosting, Store publication, Google verification, or independent security audit has been completed. See [Security and privacy](SECURITY.md) for release limits and data handling.

## Troubleshooting

| Symptom                            | Check                                                                                                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Connect Gmail disabled             | Google client ID/secret, encryption key, backend availability, and `EXTENSION_ID`. Restart the backend after configuration changes.                                                   |
| Error 403 / access denied          | The account is an approved test user; Gmail API and organization policy allow access.                                                                                                 |
| Redirect mismatch                  | Google’s registered callback exactly matches `GOOGLE_REDIRECT_URI`.                                                                                                                   |
| Wrong Gmail account                | One account is connected per Chrome profile. Reconnect the address shown in the Gmail tab or use another profile.                                                                     |
| Nudge missing                      | Reload Gmail after extension install/update and open a supported Sent conversation. Group/replied/ambiguous conversations may be unavailable.                                         |
| Extension context invalidated      | Reload Inbox Nudge in `chrome://extensions`, then refresh the Gmail tab. The updated script stops stale timers and listeners after extension reloads.                                 |
| Unable to identify a conversation  | Gmail must expose a valid reference or the metadata search must find one unambiguous match. The extension refuses to guess.                                                           |
| Recipient cannot get Chrome alerts | They need the extension connected to the same API, Chrome running, incoming nudges/alerts enabled, and system notifications allowed. Otherwise the sender can confirm email fallback. |
| Email permission required          | Add `gmail.send` in Google Data Access and reconnect from the extension to approve that grant.                                                                                        |
| Email status unknown               | Do not resend. Use recent nudges → Check Gmail Sent. A missing result remains unknown.                                                                                                |
| Cooldown active                    | Wait 24 hours after a delivered, queued, sent, or ambiguous reminder.                                                                                                                 |
| Sign-in required again             | The session expired, was revoked, or Google’s Testing-mode refresh token expired. Reconnect in the extension.                                                                         |
| Revocation unconfirmed             | Stored Google credentials were erased. Also remove access in Google Account → Third-party connections.                                                                                |
