type Failure = { ok: false; error: string };
const updated: Failure = {
  ok: false,
  error: 'Inbox Nudge was updated. Refresh Gmail to reconnect.',
};

/** An orphaned content script must stop using Chrome APIs after an update. */
export function createContentRuntime(onInvalidated: () => void) {
  let disposed = false;
  const cleanups = new Set<() => void>();

  function dispose() {
    if (disposed) return;
    disposed = true;
    for (const cleanup of cleanups) {
      // Removing a Chrome listener can itself throw after invalidation.
      try {
        cleanup();
      } catch {
        /* Continue releasing the remaining resources. */
      }
    }
    cleanups.clear();
    onInvalidated();
  }

  function active() {
    if (disposed) return false;
    try {
      if (typeof chrome !== 'undefined' && chrome.runtime?.id) return true;
    } catch {
      /* The extension namespace may also become inaccessible. */
    }
    dispose();
    return false;
  }

  function onDispose(cleanup: () => void) {
    if (disposed) cleanup();
    else cleanups.add(cleanup);
    return () => {
      cleanups.delete(cleanup);
      cleanup();
    };
  }

  async function send<T extends { ok: boolean } = { ok: boolean; error?: string }>(
    message: object,
  ): Promise<T | Failure> {
    if (!active()) return updated;
    try {
      // try/await catches both a synchronous API throw and a rejected Promise.
      const response = await chrome.runtime.sendMessage(message);
      if (!active()) return updated;
      return response && typeof response.ok === 'boolean'
        ? (response as T)
        : { ok: false, error: 'Unable to reach Inbox Nudge. Try again.' };
    } catch (failure) {
      if (failure instanceof Error && /extension context invalidated/i.test(failure.message))
        dispose();
      if (!active()) return updated;
      return { ok: false, error: 'Unable to reach Inbox Nudge. Try again.' };
    }
  }

  function interval(callback: () => void, delay: number) {
    if (!active()) return () => undefined;
    const timer = window.setInterval(() => {
      if (active()) callback();
    }, delay);
    return onDispose(() => window.clearInterval(timer));
  }

  function timeout(callback: () => void, delay: number) {
    if (!active()) return () => undefined;
    const timer = window.setTimeout(() => {
      cancel();
      if (active()) callback();
    }, delay);
    const cancel = onDispose(() => window.clearTimeout(timer));
    return cancel;
  }

  return { active, send, onDispose, interval, timeout, dispose };
}
