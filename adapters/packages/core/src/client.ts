/**
 * HTTP client for the guard service.
 *
 * Failure handling follows openapi.yaml: the service answers every decision
 * with HTTP 200, so a non-2xx or an unparseable body means the service is up
 * but unhappy, and the contract says to treat that as `ask` — never `allow`.
 * A connection that never completes is a different situation: the guard is
 * simply absent, and what to do then is a local policy decision
 * (`on_unavailable`, default `allow` per the Gate spec).
 */
import { createHash } from "node:crypto"
import type { GateConfig } from "./config.ts"
import { endpoint } from "./config.ts"
import type { Logger } from "./log.ts"
import { nullLogger } from "./log.ts"
import type { DecideRequest, DecideResponse, InspectRequest, InspectResponse } from "./protocol.ts"

export type GuardFailure = {
  /**
   * unreachable: no answer at all. refused: answered non-2xx. malformed:
   * unparseable answer. not_implemented: the route does not exist (404/405/501)
   * — the service is healthy, it just does not offer this direction yet.
   */
  kind: "unreachable" | "refused" | "malformed" | "not_implemented"
  detail: string
}

export type GuardResult<T> = { ok: true; value: T } | { ok: false; failure: GuardFailure }

export type Direction = "out" | "in"

/** Stable across retries of the same tool call, so the service can dedupe. */
export function idempotencyKey(
  harness: string,
  sessionId: string | null | undefined,
  callId: string,
  direction: Direction,
): string {
  return createHash("sha256")
    .update([harness, sessionId ?? "-", callId, direction].join("|"))
    .digest("hex")
}

export class GuardClient {
  readonly config: GateConfig
  private readonly log: Logger

  constructor(config: GateConfig, log: Logger = nullLogger) {
    this.config = config
    this.log = log
  }

  decide(body: DecideRequest, key: string): Promise<GuardResult<DecideResponse>> {
    return this.post<DecideResponse>("decide", body, key, this.config.decideTimeoutMs, (value) =>
      typeof value?.decision === "string" &&
      ["allow", "deny", "ask"].includes(value.decision as string),
    )
  }

  inspect(body: InspectRequest, key: string): Promise<GuardResult<InspectResponse>> {
    return this.post<InspectResponse>("inspect", body, key, this.config.inspectTimeoutMs, (value) =>
      typeof value?.verdict === "string" &&
      ["pass", "mask", "drop"].includes(value.verdict as string),
    )
  }

  async health(timeoutMs = 3000): Promise<boolean> {
    try {
      const res = await fetch(endpoint(this.config, "health"), {
        headers: this.headers(),
        signal: AbortSignal.timeout(timeoutMs),
      })
      return res.ok
    } catch {
      return false
    }
  }

  private headers(key?: string): Record<string, string> {
    const headers: Record<string, string> = { "content-type": "application/json" }
    if (this.config.token) headers.authorization = `Bearer ${this.config.token}`
    if (key) headers["idempotency-key"] = key
    return headers
  }

  private async post<T>(
    which: "decide" | "inspect",
    body: unknown,
    key: string,
    timeoutMs: number,
    valid: (value: any) => boolean,
  ): Promise<GuardResult<T>> {
    const url = endpoint(this.config, which)
    const started = Date.now()
    let res: Response
    try {
      res = await fetch(url, {
        method: "POST",
        headers: this.headers(key),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      })
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      this.log(`guard ${which} unreachable`, { url, detail, ms: Date.now() - started })
      return { ok: false, failure: { kind: "unreachable", detail } }
    }

    if (!res.ok) {
      const detail = `HTTP ${res.status}`
      // A missing route is not a broken guard: the service simply does not
      // implement this direction yet (PostToolUse is not in the v1 contract).
      // Kept separate from `refused` so `in` can pass through while `out`
      // still escalates to ask.
      if (res.status === 404 || res.status === 405 || res.status === 501) {
        this.log(`guard ${which} not implemented`, { url, detail })
        return { ok: false, failure: { kind: "not_implemented", detail } }
      }
      this.log(`guard ${which} refused`, { url, detail, ms: Date.now() - started })
      return { ok: false, failure: { kind: "refused", detail } }
    }

    let parsed: unknown
    try {
      parsed = await res.json()
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      this.log(`guard ${which} malformed`, { url, detail })
      return { ok: false, failure: { kind: "malformed", detail } }
    }

    if (!valid(parsed)) {
      this.log(`guard ${which} malformed`, { url, detail: "unexpected response shape" })
      return { ok: false, failure: { kind: "malformed", detail: "unexpected response shape" } }
    }

    this.log(`guard ${which} ok`, { ms: Date.now() - started })
    return { ok: true, value: parsed as T }
  }
}
