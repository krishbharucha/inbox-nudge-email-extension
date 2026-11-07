import { describe, expect, it } from 'vitest';
import { canShowReminder, resolveGmailReference, sentRoute } from '../extension/gmail';

const eligible = {
  hash: '#sent/abc123',
  accountEmail: 'alex@example.com',
  authenticatedEmail: 'alex@example.com',
  senderEmails: ['alex@example.com'],
  recipientEmails: ['maya@example.com'],
  subject: 'Hello',
};

describe('conservative Gmail UI eligibility', () => {
  it('uses account-local legacy references and rejects ambiguous thread hints', () => {
    expect(
      resolveGmailReference({
        hash: '#sent/FMfcgxOpaque',
        threadIds: ['18abcdef01234567'],
        messageIds: [],
      }),
    ).toEqual({ kind: 'thread', id: '18abcdef01234567' });
    expect(
      resolveGmailReference({
        hash: '#sent/FMfcgxOpaque',
        threadIds: [],
        messageIds: ['18abcdef01234568'],
      }),
    ).toEqual({ kind: 'message', id: '18abcdef01234568' });
    expect(
      resolveGmailReference({ hash: '#sent/FMfcgxOpaque', threadIds: [], messageIds: [] }),
    ).toBeNull();
    expect(
      resolveGmailReference({
        hash: '#sent/18abcdef01234567',
        threadIds: ['18abcdef01234567', '18abcdef01234568'],
        messageIds: [],
      }),
    ).toBeNull();
  });
  it('accepts only an opened sent conversation', () => {
    expect(canShowReminder(eligible)).toBe(true);
    for (const hash of ['#sent', '#inbox/abc', '#search/query/abc', '#sent/abc/extra'])
      expect(sentRoute(hash)).toBe(false);
  });
  it('hides the button for replies, group mail, and uncertain identity', () => {
    expect(
      canShowReminder({ ...eligible, senderEmails: ['alex@example.com', 'maya@example.com'] }),
    ).toBe(false);
    expect(
      canShowReminder({ ...eligible, recipientEmails: ['maya@example.com', 'jordan@example.com'] }),
    ).toBe(false);
    expect(canShowReminder({ ...eligible, accountEmail: null })).toBe(false);
    expect(canShowReminder({ ...eligible, accountEmail: 'other@example.com' })).toBe(false);
    expect(canShowReminder({ ...eligible, subject: '' })).toBe(false);
    expect(canShowReminder({ ...eligible, senderEmails: [] })).toBe(false);
    expect(canShowReminder({ ...eligible, recipientEmails: ['alex@example.com'] })).toBe(false);
  });
});
