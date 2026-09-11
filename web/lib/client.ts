// The browser side. Same-origin only: every URL here starts with /api, and the proxy does the rest.
import { isApiError } from "./types";

export class OpsError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId: string | null, public details: Record<string, unknown> = {}) {
    super(message);
  }
  /** The sentence a human reads. A 409 says the pause was already answered; a 502 says the API is down. */
  human(): string {
    return `${this.message} (${this.code}, HTTP ${this.status}${this.requestId ? `, request ${this.requestId}` : ""})`;
  }
}

export function describe(err: unknown): string {
  if (err instanceof OpsError) return err.human();
  return err instanceof Error ? err.message : String(err);
}

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, cache: "no-store", headers: { Accept: "application/json", ...(init?.headers ?? {}) } });
  const requestId = res.headers.get("X-Request-ID");
  let body: unknown = null;
  try { body = await res.json(); } catch { /* an empty or non-JSON body: reported by status below */ }
  if (!res.ok) {
    if (isApiError(body)) throw new OpsError(res.status, body.error.code, body.error.message, requestId, body.error.details);
    throw new OpsError(res.status, "HTTP_ERROR", `HTTP ${res.status} from ${path}`, requestId);
  }
  return body as T;
}

export function postJson<T>(path: string, payload: unknown): Promise<T> {
  return getJson<T>(path, { method: "POST", body: JSON.stringify(payload), headers: { "Content-Type": "application/json" } });
}
