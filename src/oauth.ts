import { API_ORIGIN } from './config';

export async function createPkce() {
  const random = crypto.getRandomValues(new Uint8Array(48));
  const verifier = base64url(random);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

function base64url(value: Uint8Array) {
  return btoa(String.fromCharCode(...value))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

export function authStartUrl(redirect: string, challenge: string, emailReminders = false) {
  const url = new URL('/api/auth/google/start', API_ORIGIN);
  url.searchParams.set('redirect_uri', redirect);
  url.searchParams.set('code_challenge', challenge);
  if (emailReminders) url.searchParams.set('email_reminders', 'true');
  return url.toString();
}
