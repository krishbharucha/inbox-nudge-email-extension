import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createContentRuntime } from '../extension/content-runtime';

const api = {
  id: 'test-extension' as string | undefined,
  sendMessage: vi.fn<(message: object) => Promise<unknown>>(),
};

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(api, 'id', { configurable: true, writable: true, value: 'test-extension' });
  api.sendMessage.mockReset();
  vi.stubGlobal('chrome', { runtime: api });
  vi.stubGlobal('window', globalThis);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Gmail content script lifecycle', () => {
  it('returns normal replies without changing an active script', async () => {
    api.sendMessage.mockResolvedValue({ ok: true, email: 'sender@gmail.com' });
    const stopped = vi.fn(),
      runtime = createContentRuntime(stopped);
    await expect(runtime.send({ type: 'GMAIL_CONTEXT' })).resolves.toEqual({
      ok: true,
      email: 'sender@gmail.com',
    });
    expect(runtime.active()).toBe(true);
    expect(stopped).not.toHaveBeenCalled();
  });

  it('catches synchronous invalidation, cleans up once and never sends again', async () => {
    api.sendMessage.mockImplementation(() => {
      throw new Error('Extension context invalidated.');
    });
    const stopped = vi.fn(),
      cleanup = vi.fn(),
      runtime = createContentRuntime(stopped);
    runtime.onDispose(() => {
      throw new Error('Listener namespace is gone');
    });
    runtime.onDispose(cleanup);
    await expect(runtime.send({ type: 'GMAIL_CONTEXT' })).resolves.toMatchObject({
      ok: false,
      error: expect.stringContaining('Refresh Gmail'),
    });
    await expect(runtime.send({ type: 'SUBMIT_REMINDER' })).resolves.toMatchObject({ ok: false });
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(cleanup).toHaveBeenCalledTimes(1);
  });

  it('also catches asynchronous context invalidation', async () => {
    api.sendMessage.mockRejectedValue(new Error('Extension context invalidated.'));
    const stopped = vi.fn(),
      runtime = createContentRuntime(stopped);
    await expect(runtime.send({ type: 'OPEN_EXTENSION' })).resolves.toMatchObject({ ok: false });
    expect(runtime.active()).toBe(false);
    expect(stopped).toHaveBeenCalledTimes(1);
  });

  it('cancels polling and pending DOM rendering when the runtime ID disappears', () => {
    const stopped = vi.fn(),
      callback = vi.fn(),
      runtime = createContentRuntime(stopped);
    runtime.interval(callback, 10);
    runtime.timeout(callback, 30);
    api.id = undefined;
    vi.advanceTimersByTime(1000);
    expect(callback).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it('does not access a Chrome namespace that throws after an update', async () => {
    Object.defineProperty(api, 'id', {
      configurable: true,
      get() {
        throw new Error('Extension context invalidated.');
      },
    });
    const runtime = createContentRuntime(vi.fn());
    await expect(runtime.send({ type: 'GMAIL_CONTEXT' })).resolves.toMatchObject({ ok: false });
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it('discards an in-flight reply if the extension was invalidated before it arrived', async () => {
    let complete: (value: unknown) => void = () => undefined;
    api.sendMessage.mockReturnValue(
      new Promise((resolve) => {
        complete = resolve;
      }),
    );
    const stopped = vi.fn(),
      runtime = createContentRuntime(stopped);
    const reply = runtime.send({ type: 'PREPARE_REMINDER' });
    api.id = undefined;
    complete({ ok: true, ticket: 'obsolete-ticket' });
    await expect(reply).resolves.toMatchObject({ ok: false });
    expect(stopped).toHaveBeenCalledTimes(1);
  });

  it('allows recovery from a transient message failure without retrying a submission', async () => {
    api.sendMessage
      .mockRejectedValueOnce(
        new Error('Could not establish connection. Receiving end does not exist.'),
      )
      .mockResolvedValueOnce({ ok: true });
    const stopped = vi.fn(),
      runtime = createContentRuntime(stopped);
    await expect(runtime.send({ type: 'SUBMIT_REMINDER' })).resolves.toMatchObject({ ok: false });
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    expect(runtime.active()).toBe(true);
    await expect(runtime.send({ type: 'GMAIL_CONTEXT' })).resolves.toEqual({ ok: true });
    expect(stopped).not.toHaveBeenCalled();
  });
});
