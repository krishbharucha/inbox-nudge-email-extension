# Chrome Web Store listing draft

Draft only. Fill in the real publisher/support URLs and validate claims against the final hosted build before submission.

Name: Inbox Nudge

Short description: A Nudge button in Gmail sent conversations. Connect once and stay in Gmail.

Single purpose: Help Gmail users remind recipients about unanswered, verified, single-recipient email conversations.

Description:

Inbox Nudge adds a Nudge button to supported conversations in Gmail’s Sent view. Connect Gmail in the extension’s setup screen, open a sent conversation, and choose Nudge.

Recipients with a connected Inbox Nudge extension can receive a Chrome alert and see a reminder in Gmail. Other recipients can receive a short reminder email after the sender reviews the preview and explicitly confirms email delivery. No separate website dashboard is required.

Use the extension popup to pause incoming nudges, control Chrome alerts, block senders, inspect recent outcomes, sign out or disconnect Google. Cooldowns and daily limits help keep reminders considerate. Email reminders include a sender-specific opt-out link that requires no account.

Google permissions verify conversations and, when separately granted, send confirmed email reminders. The service fetches metadata and does not store original email bodies. Google credentials are encrypted on the backend. See [PUBLIC PRIVACY URL] for all data handling, retention and deletion details.

Chrome/browser/network/device settings affect alerts. The extension does not change Gmail’s native inbox ordering, guarantee that a notification appears or confirm that an email was read. Group, replied, ambiguous or unsupported Gmail views may not offer a nudge.

Support: [SUPPORT URL / EMAIL]

## Permission justifications

| Permission/access              | Purpose                                                                                                           |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| identity                       | Open the Google consent flow and finish a client-bound handoff.                                                   |
| storage                        | Keep app session credentials in restricted extension storage, settings/session state and alert deduplication IDs. |
| alarms                         | Check for incoming nudges while Chrome runs.                                                                      |
| notifications                  | Display recipient alerts and open the verified original Gmail conversation on click.                              |
| mail.google.com content script | Add Nudge, reminder banners and inbox indicators to Gmail; read the limited DOM hints needed for those features.  |
| Exact HTTPS API host           | Authenticate, verify conversations, apply controls and deliver requested reminders.                               |

No all-sites, cookies, browser-history or tabs permission is requested. All executable code is packaged; no remotely hosted code is used.

## Privacy form preparation

Disclose identity/contact data, authentication information, personal communications metadata and limited Gmail website content used for the feature. Do not claim the product collects no data merely because it avoids email bodies. Report backend hosting and any logs or additional telemetry introduced by the final deployment. No advertising, data sales or unrelated browsing analytics are implemented. Make all Limited Use certifications consistent with the actual operation and published policy.
