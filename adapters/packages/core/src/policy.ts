/**
 * Turns (mode, guard answer) into what the adapter actually does.
 *
 * Kept out of the harness adapters on purpose: opencode 1.x, Kilo and
 * opencode 2.0 reach this point through three different hooks, and the mode
 * rules must not drift between them.
 */
import type { Mode, OnUnavailable } from "./config.ts"
import type { GuardFailure, GuardResult } from "./client.ts"
import type { DecideResponse, InspectResponse } from "./protocol.ts"

export type OutStatus = "allow" | "ask" | "deny"

export type OutAction = {
  status: OutStatus
  /** Text for the model when denied; shown to the human as context when asking. */
  reason: string
  suggest: string
  /** Who decided, for logs and for the badge. */
  source: "mode" | "guard" | "unavailable" | "rules"
  ruleId: string | null
}

export type InAction =
  | { action: "pass"; source: "mode" | "guard" | "unavailable" }
  | { action: "replace"; output: string; reason: string; source: "guard" | "unavailable" }

/**
 * `ruleStatus` is what the harness's own rules already decided. On the patched
 * build it is real; on the fallback path there is nothing to inherit and the
 * caller passes "ask".
 */
export function resolveOut(
  mode: Mode,
  ruleStatus: OutStatus,
  result: GuardResult<DecideResponse> | null,
  onUnavailable: OnUnavailable,
): OutAction {
  const inherit = (source: OutAction["source"]): OutAction => ({
    status: ruleStatus,
    reason: "",
    suggest: "",
    source,
    ruleId: null,
  })

  if (mode === "off") return inherit("rules")
  // In these modes the guard is not consulted for outgoing calls at all.
  if (mode === "allow") return { ...inherit("mode"), status: "allow" }
  if (mode === "ask") return { ...inherit("mode"), status: "ask" }
  if (!result) return inherit("rules")

  if (result.ok) {
    const { decision, reason, suggest, rule_id } = result.value
    return {
      status: decision,
      reason: reason ?? "",
      suggest: suggest ?? "",
      source: "guard",
      ruleId: rule_id ?? null,
    }
  }

  return { ...unavailableOut(result.failure, onUnavailable, ruleStatus), source: "unavailable" }
}

/**
 * A service that answered badly is not the same as a service that is gone.
 * openapi.yaml is explicit that a refused or unparseable answer must never
 * become `allow`; only a total absence falls back to local policy.
 */
function unavailableOut(
  failure: GuardFailure,
  onUnavailable: OnUnavailable,
  ruleStatus: OutStatus,
): OutAction {
  const base = { reason: `gate: guard unavailable (${failure.detail})`, suggest: "", ruleId: null }
  if (failure.kind !== "unreachable") return { ...base, status: "ask", source: "unavailable" }
  if (onUnavailable === "deny") return { ...base, status: "deny", source: "unavailable" }
  if (onUnavailable === "ask") return { ...base, status: "ask", source: "unavailable" }
  return { ...base, status: ruleStatus, reason: "", source: "unavailable" }
}

export function resolveIn(
  mode: Mode,
  result: GuardResult<InspectResponse> | null,
  original: string,
  onUnavailable: OnUnavailable,
): InAction {
  if (mode === "off" || !result) return { action: "pass", source: "mode" }

  if (result.ok) {
    const { verdict, output, reason } = result.value
    if (verdict === "pass") return { action: "pass", source: "guard" }
    if (verdict === "drop") {
      return {
        action: "replace",
        output: `[gate] result removed: ${reason || "flagged by policy"}`,
        reason: reason ?? "",
        source: "guard",
      }
    }
    // mask: the service owns the rewrite. Without replacement text there is
    // nothing to substitute, so the original stands rather than being guessed at.
    if (typeof output === "string") {
      return { action: "replace", output, reason: reason ?? "", source: "guard" }
    }
    return { action: "pass", source: "guard" }
  }

  // The service answered, but has no inspect route: it does not do PostToolUse
  // yet. Withholding every tool result would break the agent over a feature the
  // guard never claimed to have, so the result passes through untouched.
  if (result.failure.kind === "not_implemented") {
    return { action: "pass", source: "unavailable" }
  }
  if (result.failure.kind === "unreachable" && onUnavailable === "allow") {
    return { action: "pass", source: "unavailable" }
  }
  if (onUnavailable === "deny" || result.failure.kind !== "unreachable") {
    return {
      action: "replace",
      output: `[gate] result withheld: guard unavailable (${result.failure.detail})`,
      reason: result.failure.detail,
      source: "unavailable",
    }
  }
  return { action: "pass", source: "unavailable" }
}

/** Recognisable prefix so the second inspection pass can skip our own text. */
export const DENY_PREFIX = "Blocked by AgentGate policy"

/** True for text this plugin itself wrote, which never needs inspecting. */
export function isOwnMessage(text: string): boolean {
  return text.startsWith(DENY_PREFIX) || text.startsWith("[gate]")
}

/** Wording handed to the model when a call is blocked. */
export function denyMessage(action: OutAction): string {
  const rule = action.ruleId ?? "stage-2"
  const lines = [
    `${DENY_PREFIX} (${rule}): ${action.reason || "no reason given"}`,
    "Do not attempt the same thing by another route.",
  ]
  if (action.suggest) lines.push(action.suggest)
  return lines.join("\n")
}
