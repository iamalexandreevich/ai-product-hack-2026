/**
 * AgentGate for DeepSeek Harness.
 *
 * The harness exposes exactly the two seams the gate needs, so nothing is
 * patched and nothing is polyfilled:
 *   tools/pre-execute  -> PreToolDecision  {allow} | {deny,reason} | {ask,reason?}
 *   tools/post-execute -> PostToolDecision {accept,content?} | {block,feedback}
 * `ask` is routed by the harness through its own approval seam, so the
 * human-in-the-loop prompt is the native one.
 */
import { randomUUID } from "node:crypto"
import {
  GuardClient,
  buildDecideRequest,
  buildInspectRequest,
  denyMessage,
  idempotencyKey,
  loadConfig,
  mapToolCall,
  readMode,
  resolveIn,
  resolveOut,
  createLogger,
  readRules,
  History,
  // Relative, not "@agentgate/gate-core": dsh loads this file through a symlink
  // and Node resolves the real path, so a relative import works from either —
  // and the repo's own tests no longer need an install-time link to exist.
} from "../../core/src/index.ts"

export const name = "gate"
export const inject = ["tools"]

const HARNESS = { name: "dsh", version: process.env.DSH_VERSION ?? "unknown", patched: false }

export function apply(ctx) {
  const config = loadConfig()
  const history = new History()
  const log = createLogger(config.logPath)
  const client = new GuardClient(config, log)
  log(`[dsh] gate plugin loaded (guard=${config.url})`)

  const contextFor = (exec, callId) => ({
    harness: HARNESS,
    sessionId: exec.session?.id ?? null,
    callId,
    userRequest: "",
    history: history.forRequest(exec.session?.id ?? null),
    mode: readMode(config.statePath),
    rules: readRules(config.rulesPath),
    profileId: config.profileId,
    model: config.model,
    // exec.agent is a cordis service proxy: reading or serialising it outside an
    // injected scope throws, so only a plain string id is ever carried through.
    agentId: typeof exec.agent === "string" ? exec.agent : undefined,
  })

  // ---- out direction -------------------------------------------------------
  ctx.on("tools/pre-execute", async (exec, next) => {
   try {
    const mode = readMode(config.statePath)
    if (mode === "off") return next()

    const callId = exec.id ?? exec.callId ?? randomUUID()
    const args = exec.arguments ?? exec.args ?? {}
    const action = mapToolCall(exec.name, args, process.cwd())
    history.recordToolCall(exec.session?.id ?? null, exec.name, callId, action.raw)
    const context = contextFor(exec, callId)

    const result =
      mode === "allow"
        ? null
        : await client.decide(
            buildDecideRequest(action, context),
            idempotencyKey(HARNESS.name, context.sessionId, callId, "out"),
          )

    // The baseline is "allow" in the sense of "the harness would have handled
    // this on its own": returning {kind:"allow"} here SKIPS the harness's own
    // approval seam, so we must only do that on a real guard verdict. When the
    // guard is simply gone we hand the call back instead of rubber-stamping it —
    // failing open to the harness default, never to a bypass.
    const decision = resolveOut(mode, "allow", result, config.onUnavailable)
    log(`[dsh] pre ${exec.name} -> ${decision.status} (${decision.source})`)

    if (decision.status === "deny") return { kind: "deny", reason: denyMessage(decision) }
    if (decision.status === "ask") return { kind: "ask", reason: decision.reason || undefined }
    if (decision.source === "unavailable" || decision.source === "rules") return next()
    return { kind: "allow" }
   } catch (error) {
    // A broken gate must never break the agent: log and fall through.
    log(`[dsh] pre ${exec?.name} failed: ${error?.stack ?? error}`)
    return next()
   }
  })

  // ---- in direction --------------------------------------------------------
  ctx.on("tools/post-execute", async (exec, result, next) => {
   try {
    const mode = readMode(config.statePath)
    if (mode === "off") return next()

    const text = (result?.content ?? [])
      .filter((block) => block?.type === "text")
      .map((block) => block.text)
      .join("\n")
    if (!text) return next()

    const callId = exec.id ?? exec.callId ?? randomUUID()
    const action = mapToolCall(exec.name, exec.arguments ?? exec.args ?? {}, process.cwd())
    history.recordToolResult(exec.session?.id ?? null, exec.name, callId, text)
    const context = contextFor(exec, callId)
    const status = result?.isError ? "error" : "completed"

    const inspected = await client.inspect(
      buildInspectRequest(action, context, { status, output: text }),
      idempotencyKey(HARNESS.name, context.sessionId, callId, "in"),
    )

    const verdict = resolveIn(mode, inspected, text, config.onUnavailable)
    log(`[dsh] post ${exec.name} -> ${verdict.action} (${verdict.source})`)

    // core collapses mask and drop into one `replace` carrying the final text,
    // so the harness only ever sees a substituted result — same invariant the
    // other adapters keep (the session stores the rewritten text, not the original).
    if (verdict.action === "replace")
      return { kind: "accept", content: [{ type: "text", text: verdict.output }] }
    return next()
   } catch (error) {
    log(`[dsh] post ${exec?.name} failed: ${error?.stack ?? error}`)
    return next()
   }
  })
}
