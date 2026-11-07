# Inbox Nudge privacy policy — draft, not for publication

Operator: [LEGAL NAME / BUSINESS ADDRESS AS APPLICABLE]

Contact: [SUPPORT / PRIVACY EMAIL]

Effective date: [DATE]

Complete the placeholders and operational decisions below before publishing. This describes the current implementation and does not claim a deployed or legally reviewed policy.

## Information handled

Inbox Nudge uses Google account identity (name, email address and provider identifier), encrypted Google authorization credentials, app-session credentials, verified conversation metadata (subject, sender/recipient addresses, mailbox-scoped thread/message references and timestamps), reminder outcomes, preferences, blocked-sender lists and device readiness records. The extension reads the limited Gmail DOM hints needed to show its button and indicators. Original email bodies are not saved by the service.

An opaque app refresh credential is kept in restricted Chrome local storage; a short-lived access token and temporary confirmations use restricted session storage. Google credentials remain encrypted on the backend. Optional website notification subscriptions are encrypted if that separate channel is enabled.

Shared API rate counters use short-lived HMAC-derived address keys, not raw IP addresses. Specify any additional network/server logs the hosting provider retains: [LOG FIELDS AND RETENTION].

## Purpose and sharing

Data is used to connect Gmail, verify eligible conversations, deliver user-requested nudges, apply recipient controls, prevent duplicate/abusive requests, and maintain security. Google processes authorization and Gmail API operations. [HOSTING AND DATABASE PROVIDERS] process data to operate the service. Chrome/OS notification services display recipient alerts; the default alert text omits the subject and addresses.

The implementation has no advertising, sale of personal data, unrelated browsing analytics, or model-training integration. Limit all use/transfer of Google data to the applicable [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including Limited Use requirements, and keep the actual operator’s practices consistent with this statement. Disclose required legal/security/business-transfer circumstances precisely before publishing: [TRANSFER DETAILS].

## User control

Users can pause incoming nudges, disable Chrome alerts, block senders, dismiss reminders, sign out, and disconnect Google through the extension. Nonparticipants can stop reminders from a sender through the email’s opt-out link without signing in. Disconnect removes stored Google credentials and invalidates app sessions; it does not delete original Gmail messages or all application history.

For access, correction or deletion requests contact [PRIVACY EMAIL]. Before publication implement and test the verified deletion process: [VERIFICATION METHOD, SCOPE, RESPONSE PROCESS, SHARED-RECORD HANDLING AND SUPPRESSION RULES]. Uninstalling the extension alone cannot reliably instruct an offline backend to erase data.

## Retention and protection

App refresh sessions expire after thirty days; rotation does not extend that lifetime. Device readiness becomes stale after seven days; abandoned device records are pruned during registration after thirty days. Rate-limit keys are cleaned on subsequent traffic after becoming stale. Conversation/reminder history currently has no automatic deletion schedule. Choose and implement [HISTORY/AUDIT RETENTION], [ACCOUNT-DELETION RULES] and [BACKUP EXPIRATION] before publishing; do not replace these with unsupported promises.

Google credentials are encrypted on the backend; production traffic uses HTTPS and verified database TLS. Chrome credential storage is limited to trusted extension contexts. These protections do not make local Chrome storage a hardware secret vault or make every stored metadata field end-to-end encrypted. Configure and disclose [MANAGED STORAGE/BACKUP ENCRYPTION AND ACCESS CONTROLS].

## Other policy details

Finalize [DATA LOCATIONS / INTERNATIONAL TRANSFERS], [APPLICABLE AGE REQUIREMENTS], [USER RIGHTS / CONTACT PROCEDURE] and [NOTICE OF POLICY CHANGES] for the actual operator and audience before public use. Publish this policy on a public URL linked from the product pages, Google consent screen and Store listing.
