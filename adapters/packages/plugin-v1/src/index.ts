/**
 * Gate server plugin for opencode 1.x and Kilo CLI.
 *
 * Two decision paths, chosen at runtime by whether the harness actually calls
 * the `permission.ask` hook:
 *
 *   patched build  - the hook fires, and we replace the effective rule decision
 *                    for every permission request, tool or not. `allow` runs
 *                    silently, `ask` raises the harness's own Run/Deny dialog
 *                    even for a call the rules would have waved through, and
 *                    `deny` blocks that one call with a reason.
 *
 *   stock build    - the hook is declared in the SDK types but never invoked
 *                    (verified on opencode 1.17.18 and kilo 7.5.6). The
 *                    decision moves to `tool.execute.before`, deny becomes a
 *                    thrown error, and `allow` is applied by answering the
 *                    permission prompt as soon as it appears — so the prompt
 *                    flickers. Observed hook order makes this safe:
 *                    before -> permission.asked -> after.
 *
 * Tool results are filtered in `tool.execute.after`, with
 * `experimental.chat.messages.transform` as a second pass because `after` does
 * not fire when a tool throws (verified: 4 `before`, 3 `after` in one session).
 */
import {
  GuardClient,
  InspectCache,
  VerdictBook,
  buildDecideRequest,
  buildInspectRequest,
  createLogger,
  cycle,
  denyMessage,
  idempotencyKey,
  isMode,
  isOwnMessage,
  loadConfig,
  mapPermission,
  mapToolCall,
  readMode,
  resolveIn,
  resolveOut,
  writeMode,
  readRules,
} from "../../core/src/index.ts"
import type { CallContext, GateConfig, Mode, OutAction } from "../../core/src/index.ts"
import { HarnessState } from "./harness.ts"
import { SessionContext } from "./session-context.ts"

/** Marks a result as already inspected, so the second pass skips it. */
const INSPECTED = "gateInspected"

const plugin = async ({ client, directory, worktree }: any, options: any = {}) => {
  const config: GateConfig = loadConfig(options ?? {})
  const log = createLogger(config.logPath)
  const guard = new GuardClient(config, log)
  const harness = new HarnessState()
  const inspectCache = new InspectCache()
  const verdicts = new VerdictBook<OutAction>()
  const sessions = new SessionContext(async (id) => {
    const response = await client.session.get({ path: { id } })
    return (response?.data ?? response) ?? null
  })

  const cwd = worktree || directory || process.cwd()
  const mode = (): Mode => readMode(config.statePath)

  log("gate plugin loaded", { harness: harness.name, url: config.url, cwd })

  async function contextFor(sessionId: string, callId: string): Promise<CallContext> {
    return {
      harness: harness.info(),
      sessionId,
      callId,
      userRequest: await sessions.userRequestFor(sessionId),
      history: await sessions.historyFor(sessionId),
      mode: mode(),
      rules: readRules(config.rulesPath),
      profileId: config.profileId,
      model: config.model,
      parentSessionId: await sessions.parent(sessionId),
    }
  }

  /** One guard round trip for one outgoing call. */
  async function decideOutgoing(
    action: ReturnType<typeof mapToolCall>,
    sessionId: string,
    callId: string,
    ruleStatus: "allow" | "ask" | "deny",
  ): Promise<OutAction> {
    const current = mode()
    if (current !== "auto") return resolveOut(current, ruleStatus, null, config.onUnavailable)

    const context = await contextFor(sessionId, callId)
    const body = buildDecideRequest(action, context)
    const key = idempotencyKey(harness.name, sessionId, callId, "out")
    const result = await guard.decide(body, key)
    const decided = resolveOut("auto", ruleStatus, result, config.onUnavailable)
    log("out", {
      tool: action.toolName,
      callId,
      status: decided.status,
      source: decided.source,
      rule: decided.ruleId,
      turns: context.history?.length ?? 0,
    })
    return decided
  }

  /** One guard round trip for one tool result. */
  async function inspectIncoming(
    action: ReturnType<typeof mapToolCall>,
    sessionId: string,
    callId: string,
    status: "completed" | "error",
    output: string,
  ): Promise<string | null> {
    const current = mode()
    if (current === "off" || !output) return null

    const cached = inspectCache.get(output)
    const result = cached
      ? ({ ok: true, value: cached } as const)
      : await guard.inspect(
          buildInspectRequest(action, await contextFor(sessionId, callId), { status, output }),
          idempotencyKey(harness.name, sessionId, callId, "in"),
        )

    if (result.ok && !cached) inspectCache.set(output, result.value)

    const decided = resolveIn(current, result, output, config.onUnavailable)
    if (decided.action === "pass") return null
    log("in", { tool: action.toolName, callId, replaced: true, reason: decided.reason })
    return decided.output
  }

  return {
    /** Only real human input reaches this hook, which is what makes it usable
     *  as the provenance anchor for `user_request`. */
    "chat.message": async (input: any, output: any) => {
      const text = (output?.parts ?? [])
        .filter((part: any) => part?.type === "text")
        .map((part: any) => part.text)
        .join("\n")
      const sessionId = input?.sessionID ?? output?.message?.sessionID
      sessions.recordHumanMessage(sessionId, text, output?.message?.id)
    },

    /** Patched build only. Declared in the SDK on stock builds but never called. */
    "permission.ask": async (input: any, output: any) => {
      harness.markPatched()
      if (mode() === "off") return
      const callId = input?.tool?.callID ?? input?.id
      // A single tool call can raise several permission checks (e.g. the tool
      // itself plus external_directory). Decide once per callID and reuse it,
      // so the guard is asked once and the verdict is consistent.
      const existing = verdicts.peek(callId)
      const decided =
        existing ??
        (await decideOutgoing(
          mapPermission(input?.permission ?? "unknown", input?.metadata ?? {}, cwd),
          input?.sessionID,
          callId,
          output?.status ?? "ask",
        ))
      if (!existing) verdicts.set(callId, decided)
      output.status = decided.status
      if (decided.status === "deny") output.message = denyMessage(decided)
      else if (decided.reason) output.message = decided.reason
    },

    "tool.execute.before": async (input: any, output: any) => {
      if (mode() === "off") return
      // On a patched build the permission hook already decided this call.
      if (harness.patched && verdicts.peek(input.callID)) return

      const action = mapToolCall(input.tool, output?.args ?? {}, cwd)
      sessions.history.recordToolCall(input.sessionID, input.tool, input.callID, action.raw)
      // Nothing to inherit here: the harness has not evaluated its rules yet.
      const decided = await decideOutgoing(action, input.sessionID, input.callID, "ask")
      verdicts.set(input.callID, decided)

      if (decided.status === "deny") {
        // Throwing blocks this one call and surfaces the text to the model;
        // the assistant turn continues. Replying "reject" instead would abort
        // the turn and reject every other pending request in the session.
        throw new Error(denyMessage(decided))
      }
    },

    "tool.execute.after": async (input: any, output: any) => {
      if (mode() === "off") return
      const action = mapToolCall(input.tool, input?.args ?? {}, cwd)
      sessions.history.recordToolResult(
        input.sessionID, input.tool, input.callID, String(output?.output ?? ""),
      )
      const replacement = await inspectIncoming(
        action,
        input.sessionID,
        input.callID,
        "completed",
        String(output?.output ?? ""),
      )
      if (replacement !== null) {
        output.output = replacement
        // The preview is rendered in the TUI from metadata, so scrub it too.
        if (output.metadata && typeof output.metadata === "object") {
          output.metadata[INSPECTED] = true
          if (typeof output.metadata.preview === "string") output.metadata.preview = replacement
        }
      } else if (output?.metadata && typeof output.metadata === "object") {
        output.metadata[INSPECTED] = true
      }
    },

    /**
     * Second pass. `tool.execute.after` never fires for a tool that threw, so
     * error text — which can also carry injected instructions — would otherwise
     * reach the model unfiltered.
     */
    "experimental.chat.messages.transform": async (_input: any, output: any) => {
      if (mode() === "off") return
      for (const message of output?.messages ?? []) {
        for (const part of message?.parts ?? []) {
          // The agent's own prose: what it said it was about to do, which is
          // often the only place a multi-step plan is stated outright.
          if (part?.type === "text" && message?.info?.role === "assistant") {
            sessions.history.record(
              message.info.sessionID,
              { role: "assistant", author: "agent", content: String(part.text ?? "") },
              part.id ?? undefined,
            )
            continue
          }
          if (part?.type !== "tool" || part?.state?.status !== "error") continue
          if (part.state[INSPECTED]) continue
          part.state[INSPECTED] = true
          const text = String(part.state?.error ?? "")
          // Our own deny text is not worth a round trip to the guard.
          if (isOwnMessage(text)) continue
          const action = mapToolCall(part.tool, part.state?.input ?? {}, cwd)
          const replacement = await inspectIncoming(
            action,
            message?.info?.sessionID,
            part.callID ?? "unknown",
            "error",
            text,
          )
          if (replacement !== null) part.state.error = replacement
        }
      }
    },

    /** Fallback path: apply an `allow` verdict by answering the prompt. */
    event: async ({ event }: any) => {
      if (event?.type !== "permission.asked" || harness.patched) return
      const properties = event.properties ?? {}
      const callId = properties?.tool?.callID
      const decided = callId ? verdicts.peek(callId) : undefined
      if (!decided || decided.status !== "allow") return
      try {
        await client.postSessionIdPermissionsPermissionId({
          path: { id: properties.sessionID, permissionID: properties.id },
          body: { response: "once" },
        })
        log("auto-approved prompt", { callId, permission: properties.permission })
      } catch (error) {
        log("auto-approve failed", { callId, error: String(error) })
      }
    },

    /** `/gate <mode>` works everywhere slash commands go, including the Kilo
     *  VS Code client, which never loads a TUI plugin. */
    "command.execute.before": async (input: any, output: any) => {
      if (input?.command !== "gate") return
      const argument = String(input?.arguments ?? "").trim()
      if (argument === "status" || !argument) {
        const alive = await guard.health()
        log("gate status", { mode: mode(), guard: alive })
      } else if (argument === "cycle") {
        writeMode(config.statePath, cycle(mode()))
      } else if (isMode(argument)) {
        writeMode(config.statePath, argument)
        log("mode changed", { mode: argument, via: "slash" })
      }
      // Swallow the command so it never reaches the model.
      output.parts = []
    },
  }
}

export default { id: "gate", server: plugin }
