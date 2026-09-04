/**
 * Gate extension for Pi (pi.dev, @earendil-works/pi-coding-agent).
 *
 * Pi is not an opencode/kilo fork — it is its own minimal harness with a
 * genuinely usable extension API, so this is the cleanest of the three
 * integrations and needs no patch:
 *
 *   - `pi.on("tool_call")` runs before a tool and can BLOCK it by returning
 *     `{ block: true, reason }`. `terminate: false` blocks one call while the
 *     agent keeps going — exactly deny-and-continue.
 *   - Pi has no built-in permission popup, but `ctx.ui.confirm(title, message)`
 *     is a real dialog, so `ask` is genuine per-call HITL with no baseline
 *     config and no flicker — better than the opencode fallback.
 *   - `pi.on("tool_result")` can rewrite a result by returning `{ content }`,
 *     which is the `in` direction.
 *   - `pi.registerCommand("gate")` gives `/gate <mode>` in the TUI.
 *
 * The decision logic is the shared core, identical to the other harnesses.
 */
import {
  GuardClient,
  InspectCache,
  buildDecideRequest,
  buildInspectRequest,
  createLogger,
  cycle,
  denyMessage,
  idempotencyKey,
  isMode,
  loadConfig,
  mapToolCall,
  readMode,
  resolveIn,
  resolveOut,
  writeMode,
} from "../../core/src/index.ts"
import type { GateConfig, Mode } from "../../core/src/index.ts"

/** Flattens Pi's content array to text for inspection. */
function contentToText(content: unknown): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .map((part: any) => (part?.type === "text" ? String(part.text ?? "") : ""))
    .join("\n")
}

export default function gateExtension(pi: any, options: Partial<GateConfig> = {}): void {
  const config = loadConfig(options)
  const log = createLogger(config.logPath)
  const guard = new GuardClient(config, log)
  const inspectCache = new InspectCache()
  const mode = (): Mode => readMode(config.statePath)

  // Pi tool inputs already match the harness-native shapes the mapper expects
  // (bash -> {command}, read/write -> {path}), so mapToolCall handles them.
  let lastUserRequest = ""

  const harness = { name: "pi", version: options.model ? "unknown" : "unknown", patched: false }

  // Keep the newest human message as intent for the classifier.
  pi.on?.("input", (event: any) => {
    const text = String(event?.text ?? event?.input ?? "").trim()
    if (text && !text.startsWith("/")) lastUserRequest = text
  })

  pi.on("tool_call", async (event: any, ctx: any) => {
    const current = mode()
    if (current === "off") return undefined

    const cwd = ctx?.cwd ?? process.cwd()
    const action = mapToolCall(event.toolName, event.input ?? {}, cwd)
    const sessionId = ctx?.sessionManager?.getLeafId?.() ?? null
    const callId = event.toolCallId ?? "unknown"

    let decided
    if (current === "auto") {
      const body = buildDecideRequest(action, {
        harness,
        sessionId,
        callId,
        userRequest: lastUserRequest,
        mode: current,
        profileId: config.profileId,
        model: config.model,
      })
      const result = await guard.decide(body, idempotencyKey(harness.name, sessionId, callId, "out"))
      decided = resolveOut("auto", "ask", result, config.onUnavailable)
    } else {
      // ask / allow modes do not consult the guard for outgoing calls.
      decided = resolveOut(current, "ask", null, config.onUnavailable)
    }

    log("out", { tool: event.toolName, callId, status: decided.status, source: decided.source })

    if (decided.status === "deny") {
      // terminate:false blocks this one call; the agent continues.
      return { block: true, reason: denyMessage(decided), terminate: false }
    }

    if (decided.status === "ask") {
      // Real HITL: Pi's own confirm dialog, per call. Guard only in modes that
      // have a UI; a headless run cannot prompt, so treat it as a block there.
      if (!ctx?.hasUI || typeof ctx?.ui?.confirm !== "function") {
        return { block: true, reason: `${decided.reason || "confirmation required"} (no UI to confirm)`, terminate: false }
      }
      const title = `gate: confirm ${event.toolName}`
      const message = decided.reason || "Allow this tool call?"
      const ok = await ctx.ui.confirm(title, message)
      if (!ok) return { block: true, reason: "Rejected by user via gate", terminate: false }
    }

    return undefined // allow
  })

  pi.on("tool_result", async (event: any, ctx: any) => {
    const current = mode()
    if (current === "off") return undefined

    const text = contentToText(event.content)
    if (!text) return undefined

    const cwd = ctx?.cwd ?? process.cwd()
    const action = mapToolCall(event.toolName, event.input ?? {}, cwd)
    const sessionId = ctx?.sessionManager?.getLeafId?.() ?? null
    const callId = event.toolCallId ?? "unknown"

    const cached = inspectCache.get(text)
    const result = cached
      ? ({ ok: true, value: cached } as const)
      : await guard.inspect(
          buildInspectRequest(action, {
            harness,
            sessionId,
            callId,
            userRequest: lastUserRequest,
            mode: current,
          }, { status: event.isError ? "error" : "completed", output: text }),
          idempotencyKey(harness.name, sessionId, callId, "in"),
        )
    if (result.ok && !cached) inspectCache.set(text, result.value)

    const decided = resolveIn(current, result, text, config.onUnavailable)
    if (decided.action === "pass") return undefined
    log("in", { tool: event.toolName, callId, replaced: true })
    return { content: [{ type: "text", text: decided.output }] }
  })

  pi.registerCommand?.("gate", {
    description: "Gate: show or set mode (auto|ask|allow|off|cycle|status)",
    handler: async (args: string, ctx: any) => {
      const argument = String(args ?? "").trim()
      if (!argument || argument === "status") {
        const healthy = await guard.health()
        ctx?.ui?.notify?.(`gate: ${mode()} — guard ${healthy ? "reachable" : "unreachable"}`, "info")
        return
      }
      const next = argument === "cycle" ? cycle(mode()) : argument
      if (!isMode(next)) {
        ctx?.ui?.notify?.("usage: /gate auto | ask | allow | off | cycle", "warning")
        return
      }
      writeMode(config.statePath, next)
      log("mode changed", { mode: next, via: "command" })
      ctx?.ui?.notify?.(`gate: ${next}`, next === "off" ? "warning" : "info")
    },
  })

  // Pi has no approval-mode concept, and Shift+Tab is app.thinking.cycle. Pi
  // does expose registerShortcut, so gate claims Shift+Tab for the mode cycle
  // (the installer frees thinking.cycle in keybindings.json). A dedicated
  // fallback key is registered too, in case Shift+Tab is not free.
  const cycleMode = (ctx: any): void => {
    const next = cycle(mode())
    writeMode(config.statePath, next)
    log("mode changed", { mode: next, via: "shortcut" })
    ctx?.ui?.notify?.(`gate: ${next}`, next === "off" ? "warning" : "info")
    ctx?.ui?.setStatus?.(`gate: ${next}`)
  }
  try {
    pi.registerShortcut?.("shift+tab", { description: "Gate: cycle mode", handler: cycleMode })
    pi.registerShortcut?.("ctrl+shift+g", { description: "Gate: cycle mode (fallback)", handler: cycleMode })
    log("shortcuts registered")
  } catch (error) {
    log("registerShortcut unavailable", { error: String(error) })
  }

  log("gate extension loaded (pi)", { url: config.url })
}
