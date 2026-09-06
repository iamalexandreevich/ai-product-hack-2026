/** Claude SDK hook bridge to the production adapter core. JSON in/out; no tools execute. */
import { pathToFileURL } from "node:url"
import { mapToolCall, type MappedAction } from "../../adapters/packages/core/src/mapping.ts"
import { buildDecideRequest, buildInspectRequest } from "../../adapters/packages/core/src/request.ts"
import { GuardClient, idempotencyKey } from "../../adapters/packages/core/src/client.ts"
import { loadConfig } from "../../adapters/packages/core/src/config.ts"
import { resolveOut, resolveIn, denyMessage } from "../../adapters/packages/core/src/policy.ts"

export function mapClaudeTool(name: string, input: Record<string, unknown>, cwd: string): MappedAction {
  if (name.startsWith("mcp__")) {
    const parts = name.slice(5).split("__")
    const server = parts.shift()!
    const tool = parts.join("__")
    if (!server || !tool) throw new Error("Invalid Claude MCP tool name")
    return { tool: "mcp_call", toolName: name, raw: JSON.stringify(input),
      args: { cwd, mcp: { server, tool, arguments: input } },
      provenance: { kind: "mcp", server, tool } }
  }
  const names: Record<string, string> = { Bash: "bash", Read: "read", Write: "write", WebFetch: "webfetch" }
  if (!names[name]) throw new Error(`Unsupported Claude tool: ${name}`)
  const action = mapToolCall(names[name], { ...input, filePath: input.file_path }, cwd)
  action.toolName = name
  return action
}

export async function handle(input: any) {
  const config = loadConfig(input.config, {}) // Explicit benchmark config only, no host mode/rules.
  const action = mapClaudeTool(input.tool_name, input.tool_input, input.cwd)
  const context = {
    harness: { name: "claude-code", version: input.sdk_version, patched: false },
    sessionId: input.session_id, callId: input.call_id, userRequest: input.user_request,
    mode: "auto", profileId: config.profileId, model: config.model,
    agentModel: input.agent_model, rules: input.rules,
  }
  const client = new GuardClient(config)
  if (input.operation === "decide") {
    const request = buildDecideRequest(action, context)
    const result = await client.decide(request, idempotencyKey("claude-code", input.session_id, input.call_id, "out"))
    const policy = resolveOut("auto", "ask", result, config.onUnavailable)
    return { request, result, policy, deny_message: denyMessage(policy) }
  }
  if (input.operation === "inspect") {
    const request = buildInspectRequest(action, context, { status: "completed", output: input.output })
    const result = await client.inspect(request, idempotencyKey("claude-code", input.session_id, input.call_id, "in"))
    return { request, result, policy: resolveIn("auto", result, input.output, config.onUnavailable) }
  }
  throw new Error("Unsupported bridge operation")
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let text = ""
    for await (const chunk of process.stdin) text += chunk
    process.stdout.write(JSON.stringify(await handle(JSON.parse(text))))
  } catch {
    // Never echo the input, which contains the bearer credential.
    process.stderr.write("AgentGate bridge failed\n")
    process.exitCode = 1
  }
}
