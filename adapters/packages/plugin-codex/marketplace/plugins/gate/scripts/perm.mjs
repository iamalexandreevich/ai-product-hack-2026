/** PermissionRequest — the auto-approve / human-in-the-loop seam.
 *
 * Fires only where Codex would actually prompt, which is exactly where auto mode
 * earns its keep. Emitting `{}` falls through to the native prompt, so "ask" is
 * the harness's own dialog rather than anything we render. */
import { actionFor, buildDecideRequest, client, config, contextFor, emit, HARNESS, idempotencyKey, log, mode, readEvent }
  from "./gate-common.mjs"
import { denyMessage, resolveOut } from "../vendor/core/policy.ts"

const H = "PermissionRequest"
const event = readEvent()
const current = mode()

if (current === "off" || current === "ask") { emit({}); process.exit(0) }
if (current === "allow") {
  emit({ hookSpecificOutput: { hookEventName: H, decision: { behavior: "allow", message: "gate: allow mode" } } })
  process.exit(0)
}

const action = actionFor(event)
const context = contextFor(event)
const result = await client.decide(
  buildDecideRequest(action, context),
  idempotencyKey(HARNESS.name, context.sessionId, context.callId, "out"),
)

const decision = resolveOut(current, "ask", result, config.onUnavailable)
log(`[codex] perm ${event.tool_name} -> ${decision.status} (${decision.source})`)

if (decision.status === "allow")
  emit({ hookSpecificOutput: { hookEventName: H,
    decision: { behavior: "allow", message: `gate: ${decision.reason || "auto-approved"}` } } })
else if (decision.status === "deny")
  emit({ hookSpecificOutput: { hookEventName: H,
    decision: { behavior: "deny", message: denyMessage(decision) } } })
else emit({}) // ask -> native prompt
