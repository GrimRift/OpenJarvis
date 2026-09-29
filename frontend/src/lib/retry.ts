/**
 * Keep trying a startup request until the server answers.
 *
 * The page asks the server for the model list (and whether the cloud key is
 * set) once, on load. A tab that reloads while the server is still starting
 * -- the web UI is up about 20 s before it after a restart -- got no answer,
 * kept an empty list and showed "Select model" until reloaded by hand
 * (29 September). A failed attempt is tried again every `intervalMs` until
 * `timeoutMs` has passed; then the last error is thrown, as before.
 */
export interface RetryOptions {
  intervalMs?: number;
  timeoutMs?: number;
  /** Stop quietly (the component unmounted): resolves to undefined. */
  cancelled?: () => boolean;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

export async function retryUntilAnswered<T>(
  attempt: () => Promise<T>,
  {
    intervalMs = 2000,
    // After a reboot the server can take minutes (the launcher waits up to
    // 90 s for Ollama first); 2 minutes gave up before it arrived.
    timeoutMs = 600_000,
    cancelled = () => false,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now = () => Date.now(),
  }: RetryOptions = {},
): Promise<T | undefined> {
  const deadline = now() + timeoutMs;
  for (;;) {
    try {
      return await attempt();
    } catch (err) {
      if (cancelled()) return undefined;
      if (now() + intervalMs > deadline) throw err;
      await sleep(intervalMs);
      if (cancelled()) return undefined;
    }
  }
}
