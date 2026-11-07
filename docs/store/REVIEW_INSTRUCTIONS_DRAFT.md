# Reviewer instructions — draft

Use the final production ZIP, matching Store item ID, public HTTPS API and approved Google OAuth configuration. Do not publish test passwords or real users’ mail. Provide approved test accounts only through the Store’s private reviewer instructions if Google/Chrome asks for them.

1. Install Inbox Nudge. Its own setup tab opens. Click Connect Gmail, review the data disclosure and authorize the designated test Google account.
2. Click Open Gmail, open an unanswered single-recipient conversation started by that account in Sent, and choose Nudge.
3. Connect the designated recipient in another Chrome profile using the same extension/backend. Keep incoming nudges/Chrome alerts enabled. Confirm the in-app route in the sender’s Gmail dialog; verify the recipient alert/banner and original-thread link.
4. Test recipient pause/block/dismissal and confirm the sender cannot bypass those choices.
5. For the fallback route use a designated consenting address without a participating extension. Review the explicit email text. Cancel first and confirm no email was sent. Then deliberately confirm Send email reminder and inspect the final result.
6. Open the email’s opt-out link. GET displays a confirmation; POST stops future app emails from this sender, with no recipient login.
7. Restart Chrome, test persistent connection renewal, then sign out. Reconnect, disconnect Google, and verify stored provider authorization is removed/revoked as reported.
8. Reload the extension from Chrome’s Extensions page while Gmail remains open. The old script should stop safely; refreshing Gmail should load the current script.

The extension has one purpose: reminders about verified unanswered conversations. The website dashboard is optional. The exact API host is [PUBLIC API ORIGIN]. Chrome notification acceptance does not guarantee display/read status. Supported Gmail DOM matching is conservative; ambiguous references are rejected.

Outstanding before submission: actual test addresses and ownership, live provider outcomes, public policy/support links, production access arrangements, Google approval/assessment and final screenshots. Automated tests currently mock external delivery and do not constitute this reviewer walkthrough.
