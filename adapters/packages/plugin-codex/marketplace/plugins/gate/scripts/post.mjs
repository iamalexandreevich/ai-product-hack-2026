/** PostToolUse — the `in` direction.
 *
 * Codex 0.146 rejects `updatedMCPToolOutput`, so a masked result cannot be
 * substituted in place the way the other adapters do it. We degrade to
 * `decision: "block"`, which withholds the result and hands the model our reason
 * instead — the payload never reaches the context either way. */
import { actionFor, buildInspectRequest, client, config, contextFor, emit, HARNESS, idempotencyKey, log, mode, readEvent }
  from "./gate-common.mjs"
import { resolveIn } from "../vendor/core/policy.ts"

const event = readEvent()
const current = mode()
if (current === "off") { emit({}); process.exit(0) }

const output = typeof event.tool_response === "string"
  ? event.tool_response
  : JSON.stringify(event.tool_response ?? "")
if (!output) { emit({}); process.exit(0) }

const action = actionFor(event)
const context = contextFor(event)
const result = await client.inspect(
  buildInspectRequest(action, context, { status: "completed", output }),
  idempotencyKey(HARNESS.name, context.sessionId, context.callId, "in"),
)

const verdict = resolveIn(current, result, output, config.onUnavailable)
log(`[codex] post ${event.tool_name} -> ${verdict.action} (${verdict.source})`)

emit(verdict.action === "replace"
  ? { decision: "block", reason: verdict.output }
  : {})
