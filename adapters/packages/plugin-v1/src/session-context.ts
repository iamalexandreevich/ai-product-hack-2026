/**
 * Tracks the last thing a human actually said, per session.
 *
 * This matters more than it looks: `user_request` is the only intent the
 * service gets, and in a `task` subagent the "user" message was written by the
 * parent model, not by a person. Walking to the root session keeps a human
 * request as the intent instead of the model's own words.
 */
export type SessionLookup = (sessionId: string) => Promise<{ parentID?: string | null } | null>

export class SessionContext {
  private readonly humanRequest = new Map<string, string>()
  private readonly parentOf = new Map<string, string | null>()
  private readonly lookup: SessionLookup

  constructor(lookup: SessionLookup) {
    this.lookup = lookup
  }

  /** Called from `chat.message`, which only fires for real human input. */
  recordHumanMessage(sessionId: string, text: string): void {
    if (text.trim()) this.humanRequest.set(sessionId, text.trim())
  }

  async userRequestFor(sessionId: string): Promise<string> {
    let current: string | null = sessionId
    const seen = new Set<string>()
    while (current && !seen.has(current)) {
      seen.add(current)
      const own = this.humanRequest.get(current)
      if (own) return own
      current = await this.parent(current)
    }
    return ""
  }

  async rootOf(sessionId: string): Promise<string> {
    let current = sessionId
    const seen = new Set<string>()
    while (!seen.has(current)) {
      seen.add(current)
      const next = await this.parent(current)
      if (!next) return current
      current = next
    }
    return current
  }

  async parent(sessionId: string): Promise<string | null> {
    if (this.parentOf.has(sessionId)) return this.parentOf.get(sessionId) ?? null
    let parent: string | null = null
    try {
      const session = await this.lookup(sessionId)
      parent = session?.parentID ?? null
    } catch {
      parent = null
    }
    this.parentOf.set(sessionId, parent)
    return parent
  }

  /** A session with a parent is a `task` subagent. */
  async isSubagent(sessionId: string): Promise<boolean> {
    return (await this.parent(sessionId)) !== null
  }
}
