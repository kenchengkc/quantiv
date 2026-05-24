/**
 * HMAC-signed proxy to the Railway FastAPI backend.
 *
 * The Railway service refuses any non-/health request without a valid
 * `X-Quantiv-Timestamp` + `X-Quantiv-Signature` header pair. Both sides
 * compute the signature as:
 *
 *   sha256_hmac(BACKEND_SHARED_SECRET, `${method}\n${path}\n${timestamp}\n${sha256(body)}`)
 *
 * Timestamps older than 30 s are rejected. The signed body hash binds the
 * request body to the signature so a captured Authorization header can't be
 * replayed against a different payload.
 *
 * Why this and not Clerk JWTs:
 *   - Backend stays unreachable to anyone who doesn't have BACKEND_SHARED_SECRET.
 *   - We avoid maintaining a Python-side JWKS rotation against Clerk.
 *   - Browser never talks to Railway directly — only Next.js does — so
 *     CORS stays single-origin and edge caching is possible.
 */

import crypto from 'node:crypto';

const DEFAULT_TIMEOUT_MS = 5_000;

export class BackendProxyError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function requiredEnv(): { url: string; secret: string } {
  const url = process.env.BACKEND_URL;
  const secret = process.env.BACKEND_SHARED_SECRET;
  if (!url || !secret) {
    throw new BackendProxyError(
      'BACKEND_URL or BACKEND_SHARED_SECRET not configured',
      503,
    );
  }
  // Normalize trailing slash so callers can pass either form.
  return { url: url.replace(/\/+$/, ''), secret };
}

function sha256Hex(input: string): string {
  return crypto.createHash('sha256').update(input).digest('hex');
}

function sign(secret: string, canonical: string): string {
  return crypto.createHmac('sha256', secret).update(canonical).digest('hex');
}

/**
 * POST a JSON body to the Railway backend with an HMAC signature.
 *
 * @param path  Path on the backend, e.g. `/api/ml/predict`. Must start with `/`.
 * @param body  Object to serialize as the request body.
 * @param opts.timeoutMs  Abort after this many ms (default 5000).
 *
 * Throws BackendProxyError on:
 *   - missing env (status 503)
 *   - non-2xx backend response (status mirrors the upstream)
 *   - timeout / network error (status 504)
 */
export async function proxyJsonPost<TResponse>(
  path: string,
  body: unknown,
  opts: { timeoutMs?: number } = {},
): Promise<TResponse> {
  const { url, secret } = requiredEnv();
  const target = `${url}${path}`;
  const bodyStr = JSON.stringify(body ?? {});
  const timestamp = Date.now().toString();
  const canonical = `POST\n${path}\n${timestamp}\n${sha256Hex(bodyStr)}`;
  const signature = sign(secret, canonical);

  const ctl = new AbortController();
  const timer = setTimeout(
    () => ctl.abort(new Error('backend proxy timeout')),
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );

  try {
    const res = await fetch(target, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-quantiv-timestamp': timestamp,
        'x-quantiv-signature': signature,
      },
      body: bodyStr,
      cache: 'no-store',
      signal: ctl.signal,
    });
    if (!res.ok) {
      // Mirror the upstream status so the Next.js route can decide whether
      // to fall back (5xx) or surface as-is (4xx user error).
      let detail: string;
      try {
        detail = JSON.stringify(await res.json());
      } catch {
        detail = await res.text().catch(() => '');
      }
      throw new BackendProxyError(
        `backend ${res.status}: ${detail.slice(0, 200)}`,
        res.status,
      );
    }
    return (await res.json()) as TResponse;
  } catch (err) {
    if (err instanceof BackendProxyError) throw err;
    if (err instanceof Error && err.name === 'AbortError') {
      throw new BackendProxyError('backend proxy timeout', 504);
    }
    throw new BackendProxyError(
      `backend network error: ${(err as Error).message}`,
      502,
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Whether the proxy is wired up at all (BACKEND_URL + secret both present).
 *  Routes use this to short-circuit to the fallback without paying the
 *  network round-trip during local dev. */
export function backendProxyConfigured(): boolean {
  return Boolean(process.env.BACKEND_URL && process.env.BACKEND_SHARED_SECRET);
}
