/**
 * Dialogue history carried with a decision.
 *
 * One action plus the last user message cannot tell `rm -rf ./dist` that the
 * user just asked for from the same command the agent invented after reading
 * someone else's README. Intent lives in the conversation, and a guard judging
 * without it produces needless `ask`s — which is the friction the product is
 * measured on. It is also the only way a multi-step attack becomes visible:
 * each step innocuous, the sequence not.
 *
 * Shape follows the service's v2 design: a flat list of turns, oldest first,
 * each carrying who produced it. `author` is load-bearing — inside a subagent
 * the "human" turn was written by the parent model, and without the field a
 * classifier would read the agent's own words as the user's intent.
 */
import { byteLength } from "./limits.ts"

export type TurnRole = "human" | "assistant" | "toolcall" | "toolresult"
export type TurnAuthor = "human" | "agent" | "system"

export type Turn = {
  role: TurnRole
  author: TurnAuthor
  content: string
  /** Harness-native tool name, for `toolcall` and `toolresult`. */
  tool?: string
  /** Pairs a `toolcall` with its `toolresult`. */
  call_id?: string
}

/** Server-side limits; exceeding them is refused as `ask`, so clamp before sending. */
export const HISTORY_LIMITS = {
  turns: 200,
  bytes: 128 * 1024,
} as const

/**
 * Trims a turn list to what the service accepts: drops whole turns from the
 * front, never cuts one in half. A half-turn reads as a complete one and would
 * misinform the classifier about what the agent actually did.
 *
 * Standalone because Codex builds its turns from a transcript file on every
 * hook — each hook is a separate process, so there is no `History` to hold.
 */
export function clampHistory(turns: Turn[], limits = HISTORY_LIMITS): Turn[] {
  let kept = turns.length > limits.turns ? turns.slice(-limits.turns) : turns
  while (kept.length > 0 && byteLength(JSON.stringify(kept)) > limits.bytes) {
    kept = kept.slice(1)
  }
  return kept
}

/**
 * Per-session turn log.
 *
 * Kept in the plugin's own process because no harness exposes a normalised
 * transcript: each one reports messages and tool calls through its own hooks,
 * and this is where they become one shape. Sessions are bounded by the turn cap
 * rather than by time — an agent left running for hours should not grow without
 * limit, and the oldest turns are the least useful anyway.
 */
export class History {
  private readonly bySession = new Map<string, Turn[]>()
  private readonly seen = new Set<string>()
  private readonly cap: number

  constructor(cap = HISTORY_LIMITS.turns) {
    this.cap = cap
  }

  /**
   * `key` guards against double-recording: several harness hooks see the same
   * message, and a turn repeated three times reads to a classifier as the agent
   * insisting on something.
   */
  record(sessionId: string | null | undefined, turn: Turn, key?: string): void {
    if (!sessionId || !turn.content.trim()) return
    if (key) {
      if (this.seen.has(key)) return
      this.seen.add(key)
    }
    const turns = this.bySession.get(sessionId) ?? []
    turns.push(turn)
    // Drop from the front: the newest turns are the ones that explain the action.
    if (turns.length > this.cap) turns.splice(0, turns.length - this.cap)
    this.bySession.set(sessionId, turns)
  }

  /** Marks a tool call, so its result can be paired with it later. */
  recordToolCall(sessionId: string | null | undefined, tool: string, callId: string, content: string): void {
    this.record(sessionId, { role: "toolcall", author: "agent", content, tool, call_id: callId })
  }

  recordToolResult(sessionId: string | null | undefined, tool: string, callId: string, content: string): void {
    this.record(sessionId, { role: "toolresult", author: "system", content, tool, call_id: callId })
  }

  /**
   * The turns to send, oldest first, within the server's limits. Trimming drops
   * whole turns from the front rather than cutting one in half: a truncated
   * turn reads as a complete one and would mislead the classifier. The server
   * truncates again for its own prompt budget; this only keeps the request
   * inside what it will accept.
   */
  forRequest(sessionId: string | null | undefined, limits = HISTORY_LIMITS): Turn[] {
    if (!sessionId) return []
    return clampHistory(this.bySession.get(sessionId) ?? [], limits)
  }

  /**
   * Re-attributes `human` turns to the agent that actually wrote them. Inside a
   * subagent the "user" message came from the parent model, and the difference
   * is the whole point of the field.
   */
  attributeTo(sessionId: string, author: TurnAuthor): void {
    const turns = this.bySession.get(sessionId)
    if (!turns) return
    for (const turn of turns) if (turn.role === "human") turn.author = author
  }

  /** The last thing a real person said, for `user_request`. */
  lastHumanRequest(sessionId: string | null | undefined): string {
    if (!sessionId) return ""
    const turns = this.bySession.get(sessionId) ?? []
    for (let i = turns.length - 1; i >= 0; i--) {
      const turn = turns[i]
      if (turn.role === "human" && turn.author === "human") return turn.content
    }
    return ""
  }

  forget(sessionId: string): void {
    this.bySession.delete(sessionId)
  }
}
