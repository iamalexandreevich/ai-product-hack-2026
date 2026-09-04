/**
 * Tool results repeat constantly — the same file read twice, the same command
 * run again — and inspecting them is the slow direction (15 s budget). Keyed by
 * content hash, not by call id, so a repeat of identical text is free.
 */
import { createHash } from "node:crypto"
import type { InspectResponse } from "./protocol.ts"

export function hashOutput(output: string): string {
  return createHash("sha256").update(output).digest("hex")
}

export class InspectCache {
  private readonly entries = new Map<string, InspectResponse>()
  private readonly limit: number

  constructor(limit = 500) {
    this.limit = limit
  }

  get(output: string): InspectResponse | undefined {
    return this.entries.get(hashOutput(output))
  }

  set(output: string, value: InspectResponse): void {
    // Plain FIFO eviction: the process is short-lived and this is not a hot loop.
    if (this.entries.size >= this.limit) {
      const oldest = this.entries.keys().next().value
      if (oldest !== undefined) this.entries.delete(oldest)
    }
    this.entries.set(hashOutput(output), value)
  }

  get size(): number {
    return this.entries.size
  }
}

/**
 * Verdicts computed before execution, looked up later by the permission-event
 * handler on the fallback path. Without this the guard would be called twice
 * for one tool call.
 */
export class VerdictBook<T> {
  private readonly entries = new Map<string, { value: T; at: number }>()
  private readonly ttlMs: number

  constructor(ttlMs = 120_000) {
    this.ttlMs = ttlMs
  }

  set(callId: string, value: T): void {
    this.sweep()
    this.entries.set(callId, { value, at: Date.now() })
  }

  take(callId: string): T | undefined {
    const hit = this.entries.get(callId)
    if (!hit) return undefined
    this.entries.delete(callId)
    return hit.value
  }

  peek(callId: string): T | undefined {
    return this.entries.get(callId)?.value
  }

  private sweep(): void {
    const cutoff = Date.now() - this.ttlMs
    for (const [key, entry] of this.entries) {
      if (entry.at < cutoff) this.entries.delete(key)
    }
  }
}
