/** Exercise the production request builders without running a harness or any tool. */
import assert from "node:assert/strict"
import { buildDecideRequest, buildInspectRequest } from "../../adapters/packages/core/src/request.ts"

const action = {
  tool: "file_read" as const, toolName: "Read", raw: "",
  args: { cwd: "/repo", paths: ["/repo/README.md"] },
  provenance: { kind: "file" as const, path: "/repo/README.md" },
}
const context = {
  harness: { name: "bench", version: "test", patched: true },
  sessionId: "adapter-contract", callId: "call-1", userRequest: "Read the README",
  mode: "auto", history: [{ role: "human" as const, author: "human" as const, content: "Work in /repo" }],
}
const decide = buildDecideRequest(action, context)
const text = "a".repeat(40_000) + "UNTRUSTED_TAIL"
const inspect = buildInspectRequest(action, context, { status: "completed", output: text })
const networkBodies: Record<string, unknown> = {}
for (const method of ["GET", "HEAD", "POST", "DELETE"]) {
  // Builder coverage: supply the method at the mapped-action boundary. The
  // production mapper's lack of method attribution is tracked separately.
  const network = { ...action, tool: "network" as const, toolName: "WebFetch",
    args: { cwd: "/repo", domains: ["github.com"], method },
    provenance: { kind: "web" as const, url: "https://github.com" } }
  const request = buildDecideRequest(network, context)
  const outputRequest = buildInspectRequest(network, context, { status: "completed", output: "ok" })
  assert.equal((request.args as { method?: string }).method, method)
  assert.equal((outputRequest.args as { method?: string }).method, method)
  networkBodies[`decide_network_${method}`] = request
  networkBodies[`inspect_network_${method}`] = outputRequest
}
assert.equal(decide.call_id, inspect.call_id, "decide/inspect must share a top-level call_id")
assert.deepEqual(inspect.history, context.history, "inspection must receive the dialogue")
assert.equal(inspect.output, text, "valid output beyond 32 KiB must reach inspection intact")
// Preserve over-limit text too: the service must refuse it as drop. Silently removing
// a suffix before the gate inspects it would change the input under evaluation.
const large = buildInspectRequest(action, context, { status: "error", output: "я".repeat(140_000) })
assert.equal(large.output, "я".repeat(140_000))
console.log(process.argv.includes("--json")
  ? JSON.stringify({ decide, inspect, ...networkBodies })
  : "Adapter checks passed: call_id, history, output limits, network method forwarding")
