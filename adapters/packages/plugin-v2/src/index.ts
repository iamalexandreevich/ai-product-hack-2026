/**
 * Gate plugin for opencode 2.0 — written against its REAL runtime API.
 *
 * opencode 2.0 does NOT use the v1 `{ id, server }` Hooks shape (that lives
 * under `@opencode-ai/plugin/v1` as a compat export and is never invoked by
 * the 2.0 runtime — verified: the module imports but the server factory is
 * never called). The native API is `Plugin.define({ id, setup(ctx) })` where
 * `ctx` exposes domains (`tool`, `session`, `event`, …). Gating goes through
 * `ctx.tool.hook`:
 *
 *   - "execute.before": inspect the call, throw to block it (deny), or return
 *     to allow. There is no permission domain in 2.0, so `ask` degrades to a
 *     block with an explanatory message (a headless service has no prompt).
 *   - "execute.after": rewrite the result (mask/drop) or the error text.
 *
 * The decision logic is the shared core, identical to the other harnesses.
 */
import {
  GuardClient,
  InspectCache,
  buildDecideRequest,
  buildInspectRequest,
  createLogger,
  denyMessage,
  idempotencyKey,
  loadConfig,
  mapToolCall,
  readMode,
  resolveIn,
  resolveOut,
} from "../../core/src/index.ts"
import type { GateConfig, Mode } from "../../core/src/index.ts"

/** opencode 2.0 tool results carry text in `content` (string or parts) or `output`. */
function resultText(result: any): string {
  if (!result) return ""
  if (typeof result.content === "string") return result.content
  if (Array.isArray(result.content)) {
    return result.content.map((p: any) => (typeof p?.text === "string" ? p.text : "")).join("\n")
  }
  return typeof result.output === "string" ? result.output : ""
}

function withText(result: any, text: string): any {
  return { ...result, content: text, output: typeof result?.output === "string" ? text : result?.output }
}

// opencode 2.0 loads the default export and calls setup(ctx). Plugin.define
// is an identity helper, so exporting the object directly avoids importing
// @opencode-ai/plugin (a peer dep that plugin add does not install).
export default {
  id: "gate",
  async setup(ctx: any) {
    const config: GateConfig = loadConfig(ctx.options ?? {})
    const log = createLogger(config.logPath)
    const guard = new GuardClient(config, log)
    const inspectCache = new InspectCache()
    const harness = { name: "opencode2", version: "2.0", patched: false }
    const mode = (): Mode => readMode(config.statePath)
    const cwd = ctx.app?.path?.cwd ?? ctx.app?.directory ?? process.cwd()

    log("gate plugin loaded (opencode2)", { url: config.url, cwd })

    await ctx.tool.hook("execute.before", async (e: any) => {
      const current = mode()
      if (current === "off") return
      const action = mapToolCall(e.tool, (e.input as any) ?? {}, cwd)
      const callId = String(e.id ?? "unknown")

      let decided
      if (current === "auto") {
        const body = buildDecideRequest(action, {
          harness,
          sessionId: e.sessionID ?? null,
          callId,
          userRequest: "",
          mode: current,
          profileId: config.profileId,
          model: config.model,
          agentId: e.agent,
        })
        const result = await guard.decide(body, idempotencyKey(harness.name, e.sessionID, callId, "out"))
        decided = resolveOut("auto", "ask", result, config.onUnavailable)
      } else {
        decided = resolveOut(current, "ask", null, config.onUnavailable)
      }
      log("out", { tool: e.tool, callId, status: decided.status, source: decided.source })

      // No permission domain in 2.0: block by throwing. deny and ask both stop
      // the call (ask has no prompt to show in the background service); allow
      // returns and the tool runs.
      if (decided.status === "deny") throw new Error(denyMessage(decided))
      if (decided.status === "ask") {
        throw new Error(`${decided.reason || "confirmation required"} — set gate mode to allow, or approve in a client that supports prompts`)
      }
    })

    await ctx.tool.hook("execute.after", async (e: any) => {
      const current = mode()
      if (current === "off") return
      const action = mapToolCall(e.tool, (e.input as any) ?? {}, cwd)
      const callId = String(e.id ?? "unknown")
      const isError = e.status === "error"
      const text = isError ? String(e.error?.message ?? e.error ?? "") : resultText(e.result)
      if (!text) return

      const cached = inspectCache.get(text)
      const result = cached
        ? ({ ok: true, value: cached } as const)
        : await guard.inspect(
            buildInspectRequest(
              action,
              { harness, sessionId: e.sessionID ?? null, callId, userRequest: "", mode: current },
              { status: isError ? "error" : "completed", output: text },
            ),
            idempotencyKey(harness.name, e.sessionID, callId, "in"),
          )
      if (result.ok && !cached) inspectCache.set(text, result.value)

      const decided = resolveIn(current, result, text, config.onUnavailable)
      if (decided.action === "pass") return
      log("in", { tool: e.tool, callId, replaced: true })
      if (isError) {
        e.error = { ...(e.error ?? {}), message: decided.output }
      } else {
        e.result = withText(e.result, decided.output)
      }
    })
  },
}
