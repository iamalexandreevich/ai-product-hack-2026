/** PreToolUse — the hard-deny gate.
 *
 * Codex 0.146 implements only `deny` on this event: `allow` and `ask` are in the
 * published schema but the runtime rejects them ("unsupported
 * permissionDecision:allow"). So anything that is not a deny emits `{}` and the
 * auto-approve decision is left to PermissionRequest (perm.mjs). */
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

emit(decision.status === "deny"
  ? { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny",
        permissionDecisionReason: denyMessage(decision) } }
  : {})
