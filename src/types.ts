export type Status =
  | 'delivered'
  | 'recipient_not_connected'
  | 'cooldown_active'
  | 'recipient_disabled'
  | 'thread_replied'
  | 'unable_to_verify'
  | 'rate_limited'
  | 'dismissed'
  | 'email_ready'
  | 'email_permission_required'
  | 'email_pending'
  | 'email_sending'
  | 'email_sent'
  | 'email_failed'
  | 'email_unknown';

export interface User {
  id: string;
  email: string;
  display_name: string;
  demo: boolean;
  connection_status: string;
  daily_used: number;
  daily_limit: number;
  cooldown_hours: number;
  provider?: string;
  gmail_send_enabled?: boolean;
}

export interface Reminder {
  id: string;
  thread_id: string;
  subject: string;
  preview: string;
  sender_email: string;
  sender_name: string;
  recipient_email: string;
  recipient_name: string;
  status: Status;
  direction: 'sent' | 'received';
  created_at: string;
  delivered_at: string | null;
  dismissed_at: string | null;
  simulated: boolean;
  provider_thread_id: string | null;
  delivery_channel?: 'in_app' | 'email';
  notification_status?: string;
}

export interface Thread {
  id: string;
  subject: string;
  preview: string;
  recipient_email: string;
  last_message_at: string;
  has_reply: boolean;
  eligibility: Status;
  provider_thread_id?: string;
}

export interface Preferences {
  reminders_enabled: boolean;
  notification_enabled: boolean;
  blocked_senders: string[];
}

export interface GmailConversation {
  provider_thread_id: string;
  sender_name: string;
  sender_email: string;
  subject: string;
  last_message_at: string;
  unread: boolean;
  message_count: number;
}

export interface GmailInboxPage {
  threads: GmailConversation[];
  next_page_token: string | null;
}

export interface DeliveryPlan {
  status: Status;
  simulated: boolean;
  delivery_channel: 'in_app' | 'email';
  push_available: boolean;
  email_preview: string | null;
}

export const statusLabels: Record<Status, string> = {
  delivered: 'Reminder delivered',
  recipient_not_connected: 'Recipient not connected',
  cooldown_active: 'Reminder cooldown active',
  recipient_disabled: 'Recipient disabled reminders',
  thread_replied: 'Thread already received a reply',
  unable_to_verify: 'Unable to verify eligibility',
  rate_limited: 'Daily limit reached',
  dismissed: 'Dismissed',
  email_ready: 'Email reminder available',
  email_permission_required: 'Enable email reminders',
  email_pending: 'Email reminder queued',
  email_sending: 'Sending email reminder',
  email_sent: 'Email reminder sent',
  email_failed: 'Email reminder wasn’t sent',
  email_unknown: 'Email send status unknown',
};

export const statusDetails: Record<Status, string> = {
  delivered: 'The recipient can see this reminder in Gmail with Inbox Nudge. No new email is sent.',
  recipient_not_connected:
    'The request will be recorded, but this recipient cannot be notified. Gmail-native delivery is unavailable. No email will be sent.',
  cooldown_active:
    'This conversation was nudged in the past 24 hours. Give them a little time before sending another reminder.',
  recipient_disabled:
    'This recipient isn’t accepting reminders from you. Their preferences always come first.',
  thread_replied: 'There’s already a reply in this conversation, so it doesn’t need a nudge.',
  unable_to_verify:
    'We couldn’t verify this conversation for reminder delivery. Reconnect Google or try again. No reminder was delivered.',
  rate_limited:
    'You’ve used your daily reminder allowance. Your allowance resets as requests become 24 hours old.',
  dismissed: 'The recipient has dismissed this reminder from their priority view.',
  email_ready:
    'Browser notifications aren’t available for this recipient. With your confirmation, a short reminder email will be sent from your Gmail account in the original conversation.',
  email_permission_required:
    'This recipient needs an email reminder. Enable Gmail send permission, then return here to review and confirm the email.',
  email_pending:
    'The email is queued. We will recheck the conversation and recipient preferences before sending.',
  email_sending: 'The reminder is being submitted to Gmail. Please wait for the final status.',
  email_sent:
    'Gmail accepted the reminder email. Inbox placement and device notifications depend on the recipient’s email settings.',
  email_failed:
    'Gmail did not accept the email, or verification stopped the send. Review your connection before trying a new request.',
  email_unknown:
    'Gmail may have accepted the email before the connection was interrupted. Do not resend. Check Gmail Sent to confirm.',
};
