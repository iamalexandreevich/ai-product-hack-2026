/** PreToolUse — the hard-deny gate.
 *
 * Codex 0.146 implements only `deny` on this event: `allow` and `ask` are in the
 * published schema but the runtime rejects them ("unsupported
 * permissionDecision:allow"). So anything that is not a deny emits `{}` and the
 * auto-approve decision is left to PermissionRequest (perm.mjs).
 *
 * That hand-off holds for a guard-issued `ask`: perm.mjs renders it as Codex's
 * own prompt. It does not hold when the guard is unavailable. perm.mjs fires
 * only where Codex would have prompted anyway, so on a call it would have
 * auto-approved there is no second event and nothing asks. Measured on a real
 * install pointing at an unreachable guard: six `pre Bash -> ask (unavailable)`
 * verdicts and six commands executed, with no prompt in between.
 *
 * An `ask` this harness cannot show is an `allow`. So an unavailable-`ask`
 * becomes a `deny` here -- the fail-closed promise has to hold in the one case
 * it exists for, which is the guard being gone. A guard-issued `ask` is left
 * alone, because something downstream will render it. */
import { actionFor, buildDecideRequest, client, contextFor, emit, HARNESS, idempotencyKey, log, mode, readEvent }
  from "./gate-common.mjs"
import { denyMessage, resolveOut } from "../vendor/core/policy.ts"
import { config } from "./gate-common.mjs"

const event = readEvent()
const current = mode()
if (current === "off" || current === "allow") { emit({}); process.exit(0) }

const action = actionFor(event)
const context = contextFor(event)
const result = await client.decide(
  buildDecideRequest(action, context),
  idempotencyKey(HARNESS.name, context.sessionId, context.callId, "out"),
)

const decision = resolveOut(current, "ask", result, config.onUnavailable)
log(`[codex] pre ${event.tool_name} -> ${decision.status} (${decision.source})`)

// Only the unavailable `ask` is escalated: it is the one no later event will
// show. A guard-issued `ask` keeps its route through perm.mjs.
const unshowable = decision.status === "ask" && decision.source === "unavailable"

// `onUnavailable: "allow"` leaves the reason empty, so an escalated call would
// otherwise be refused without a word. The person reading it needs to know the
// guard is gone rather than that their command was judged and rejected.
const reason = unshowable && !decision.reason
  ? "gate: guard unavailable, and this harness cannot ask — refusing instead of proceeding"
  : denyMessage(decision)

emit(decision.status === "deny" || unshowable
  ? { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny",
        permissionDecisionReason: reason } }
  : {})
