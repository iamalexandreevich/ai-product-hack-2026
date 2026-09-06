/** Offline checks of the real adapter core, including HTTP serialization and policy. */
import assert from "node:assert/strict"
import { handle, mapClaudeTool } from "./claude_gate_bridge.ts"

const config = { url: "https://guard.example", token: "test-only-token", profileId: "test", model: "primary", onUnavailable: "ask" }
const base = { config, operation: "decide", tool_name: "Bash", tool_input: { command: "git status" },
  cwd: "/sandbox", session_id: "session", call_id: "call", user_request: "Check status",
  sdk_version: "test", rules: { version: 1, level: "medium", allow: [], ask: [], deny: ["rm *"] } }
const sent: { url: string; headers: Headers; body: any }[] = []
let answer: unknown = { decision: "allow", stage: 1 }
let status = 200
globalThis.fetch = async (url, options) => {
  sent.push({ url: String(url), headers: new Headers(options?.headers), body: JSON.parse(String(options?.body)) })
  return new Response(JSON.stringify(answer), { status })
}
const allowed = await handle(base)
assert.equal(allowed.policy.status, "allow")
assert.equal(sent[0].headers.get("authorization"), "Bearer test-only-token")
assert.equal(sent[0].body.raw, "git status")
assert.equal(sent[0].body.args.cwd, "/sandbox")
assert.deepEqual(sent[0].body.rules, base.rules)
assert.equal(sent[0].body.call_id, "call")
assert.equal(sent[0].body.profile_id, "test")
assert.equal(sent[0].body.model, "primary")
assert.ok(!JSON.stringify(allowed).includes(config.token))
for (const decision of ["ask", "deny"]) {
  answer = { decision, reason: "policy", suggest: "safe alternative", stage: 2,
    cost: { input_tokens: 12, output_tokens: 4, amount: 0.01, currency: "USD" } }
  const result = await handle(base)
  assert.equal(result.policy.status, decision)
  assert.equal((result.result as any).value.cost.amount, 0.01)
}
assert.deepEqual(mapClaudeTool("Read", { file_path: "/repo/x" }, "/sandbox").args.paths, ["/repo/x"])
assert.equal(mapClaudeTool("Write", { file_path: "/repo/x", content: "new content" }, "/sandbox").raw, "new content")
const web = mapClaudeTool("WebFetch", { url: "https://example.net/x" }, "/sandbox")
assert.deepEqual(web.args.domains, ["example.net"])
const mcp = mapClaudeTool("mcp__notes_team__save__note", { nested: { count: 4 } }, "/sandbox")
assert.deepEqual(mcp.args.mcp, { server: "notes_team", tool: "save__note", arguments: { nested: { count: 4 } } })
assert.throws(() => mapClaudeTool("UnknownTool", {}, "/sandbox"))
const inspect = { ...base, operation: "inspect", output: "x".repeat(40000) + "secret" }
answer = { verdict: "mask", output: "clean", reason: "secret" }
const masked = await handle(inspect)
assert.equal(masked.policy.action, "replace")
assert.equal((masked.policy as any).output, "clean")
assert.equal(sent.at(-1)!.body.output, inspect.output)
assert.equal(sent.at(-1)!.body.call_id, sent[0].body.call_id)
assert.notEqual(sent.at(-1)!.headers.get("idempotency-key"), sent[0].headers.get("idempotency-key"))
for (const verdict of ["pass", "drop"]) {
  answer = { verdict, reason: "policy" }
  const result = await handle(inspect)
  assert.equal(result.policy.action, verdict === "pass" ? "pass" : "replace")
}
status = 401
assert.equal((await handle(base)).policy.status, "ask")
status = 200
answer = { decision: "nonsense" }
assert.equal((await handle(base)).policy.status, "ask")
globalThis.fetch = async () => { throw new Error("unreachable") }
assert.equal((await handle(base)).policy.status, "ask")
console.log("Claude AgentGate core checks passed")
