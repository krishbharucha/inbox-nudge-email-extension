/** Conservative DOM hints, never trusted backend eligibility evidence. */
export function sentRoute(hash: string) {
  return /^#sent\/[A-Za-z0-9_-]+$/.test(hash);
}

export function resolveGmailReference(input: {
  hash: string;
  threadIds: string[];
  messageIds: string[];
}): { kind: 'thread' | 'message'; id: string } | null {
  const valid = (id: string) => /^[a-fA-F0-9]{10,32}$/.test(id);
  const threadIds = [...new Set(input.threadIds.filter(valid))];
  if (threadIds.length > 1) return null;
  if (threadIds.length === 1) return { kind: 'thread', id: threadIds[0] };
  const routeId = input.hash.split('/').at(-1) ?? '';
  if (valid(routeId)) return { kind: 'thread', id: routeId };
  const messageId = input.messageIds.find(valid);
  return messageId ? { kind: 'message', id: messageId } : null;
}

export function canShowReminder(input: {
  hash: string;
  accountEmail: string | null;
  authenticatedEmail: string;
  senderEmails: string[];
  recipientEmails: string[];
  subject: string;
}) {
  const normalize = (value: string) => value.trim().toLowerCase();
  const self = normalize(input.authenticatedEmail);
  return (
    sentRoute(input.hash) &&
    input.accountEmail !== null &&
    normalize(input.accountEmail) === self &&
    input.subject.trim().length > 0 &&
    input.senderEmails.length > 0 &&
    input.senderEmails.every((email) => normalize(email) === self) &&
    input.recipientEmails.length === 1 &&
    normalize(input.recipientEmails[0]) !== self
  );
}
