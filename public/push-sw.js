/* Push-only worker: never intercepts or caches authenticated app requests. */
self.addEventListener('push', (event) => {
  let payload;
  try {
    payload = event.data?.json();
  } catch {
    return;
  }
  if (!payload || typeof payload.body !== 'string') return;
  event.waitUntil(
    self.registration.showNotification('Inbox Nudge', {
      body: payload.body.slice(0, 300),
      tag: typeof payload.tag === 'string' ? payload.tag.slice(0, 100) : 'inbox-nudge',
      icon: '/icon.svg',
      data: { url: '/?view=priority' },
    }),
  );
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = self.location.origin + '/?view=priority';
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      const existing = windows.find(
        (client) => new URL(client.url).origin === self.location.origin,
      );
      if (existing) {
        await existing.navigate(url);
        await existing.focus();
      } else await self.clients.openWindow(url);
    })(),
  );
});
