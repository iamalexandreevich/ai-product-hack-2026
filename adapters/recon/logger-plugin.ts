/**
 * Gate recon logger.
 *
 * Drop into ~/.config/opencode/plugin/ or ~/.config/kilo/plugin/ and run a
 * session. Every hook invocation and every event is appended as one JSON line
 * to GATE_RECON_LOG (default ~/.local/share/gate/recon.jsonl).
 *
 * Purpose: answer the open questions in step 0 of the plan before any real
 * code is written. This file is throwaway diagnostics, not shipped code.
 */
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const LOG_PATH =
  process.env.GATE_RECON_LOG ?? path.join(os.homedir(), ".local", "share", "gate", "recon.jsonl")

const MAX_STRING = 600

function shrink(value: unknown, depth = 0): unknown {
  if (value === null || value === undefined) return value
  if (typeof value === "string") {
    return value.length > MAX_STRING ? `${value.slice(0, MAX_STRING)}…<${value.length}b>` : value
  }
  if (typeof value !== "object") return value
  if (depth > 4) return "<deep>"
  if (Array.isArray(value)) {
    const head = value.slice(0, 20).map((item) => shrink(item, depth + 1))
    return value.length > 20 ? [...head, `<+${value.length - 20} more>`] : head
  }
  const out: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    out[key] = shrink(item, depth + 1)
  }
  return out
}

let ready = false
function write(kind: string, payload: Record<string, unknown>): void {
  try {
    if (!ready) {
      fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true })
      ready = true
    }
    fs.appendFileSync(
      LOG_PATH,
      `${JSON.stringify({ ts: Date.now(), kind, ...(shrink(payload) as object) })}\n`,
    )
  } catch {
    // never break the harness because logging failed
  }
}

/** Records which event types were seen, so the exact permission event name is known. */
const eventTypes = new Set<string>()

/** Tools we saw enter execution, to detect whether `after` fires on failures. */
const started = new Map<string, { tool: string; at: number }>()

const plugin = async ({ client, project, directory, worktree, serverUrl }: any) => {
  write("boot", {
    harness: path.basename(process.execPath),
    argv0: process.argv[0],
    node: process.version,
    directory,
    worktree,
    serverUrl,
    project: project?.id,
    clientKeys: client ? Object.keys(client) : null,
    sessionKeys: client?.session ? Object.keys(Object.getPrototypeOf(client.session)) : null,
    env: {
      KILO_PURE: process.env.KILO_PURE ?? null,
      KILO_CONFIG_CONTENT: process.env.KILO_CONFIG_CONTENT ? "<set>" : null,
      OPENCODE_CONFIG_CONTENT: process.env.OPENCODE_CONFIG_CONTENT ? "<set>" : null,
    },
  })

  /** Probes the SDK once, to pin down the exact shape the normalizer will consume. */
  let probed = false
  async function probeSession(sessionID: string): Promise<void> {
    if (probed) return
    probed = true
    try {
      const session = await client.session.get({ path: { id: sessionID } })
      const messages = await client.session.messages({ path: { id: sessionID } })
      const list = (messages as any)?.data ?? messages
      write("probe.session", {
        sessionRaw: Object.keys(session ?? {}),
        session: (session as any)?.data ?? session,
        messagesCount: Array.isArray(list) ? list.length : null,
        firstMessage: Array.isArray(list) ? list[0] : null,
        lastMessage: Array.isArray(list) ? list[list.length - 1] : null,
      })
    } catch (error) {
      write("probe.session.error", { error: String(error) })
    }
  }

  return {
    event: async ({ event }: any) => {
      const type = event?.type ?? "<none>"
      const first = !eventTypes.has(type)
      eventTypes.add(type)
      // Permission and session events are the ones the fallback path depends on;
      // everything else is logged once so the full event vocabulary is known.
      const interesting = type.startsWith("permission.") || type.startsWith("session.")
      if (interesting || first) {
        write("event", { type, first, properties: event?.properties })
      }
    },

    config: async (config: any) => {
      write("hook.config", {
        keys: Object.keys(config ?? {}),
        permission: config?.permission,
        plugin: config?.plugin,
      })
    },

    "chat.message": async (input: any, output: any) => {
      write("hook.chat.message", {
        input,
        role: output?.message?.role,
        messageID: output?.message?.id,
        sessionID: output?.message?.sessionID,
        parts: output?.parts?.map((part: any) => part?.type),
      })
    },

    "chat.params": async (input: any) => {
      write("hook.chat.params", { sessionID: input?.sessionID, agent: input?.agent })
    },

    "permission.ask": async (input: any, output: any) => {
      // If this ever appears in the log on a stock binary, the plan's core
      // assumption is wrong and the patch is unnecessary.
      write("hook.permission.ask", { input, status: output?.status })
    },

    "command.execute.before": async (input: any, output: any) => {
      write("hook.command.execute.before", {
        command: input?.command,
        sessionID: input?.sessionID,
        arguments: input?.arguments,
        parts: output?.parts?.length,
      })
    },

    "tool.execute.before": async (input: any, output: any) => {
      started.set(input?.callID, { tool: input?.tool, at: Date.now() })
      write("hook.tool.execute.before", {
        tool: input?.tool,
        sessionID: input?.sessionID,
        callID: input?.callID,
        args: output?.args,
      })
      await probeSession(input?.sessionID)
    },

    "tool.execute.after": async (input: any, output: any) => {
      const pending = started.get(input?.callID)
      started.delete(input?.callID)
      write("hook.tool.execute.after", {
        tool: input?.tool,
        sessionID: input?.sessionID,
        callID: input?.callID,
        sawBefore: Boolean(pending),
        title: output?.title,
        outputLength: typeof output?.output === "string" ? output.output.length : null,
        output: output?.output,
        metadata: output?.metadata,
      })
    },

    "experimental.chat.messages.transform": async (_input: any, output: any) => {
      const messages = output?.messages ?? []
      write("hook.messages.transform", {
        count: messages.length,
        roles: messages.map((message: any) => message?.info?.role),
        partTypes: messages.flatMap((message: any) =>
          (message?.parts ?? []).map((part: any) => part?.type),
        ),
        last: messages[messages.length - 1],
      })
    },

    "tool.definition": async (input: any) => {
      write("hook.tool.definition", { toolID: input?.toolID })
    },

    "shell.env": async (input: any) => {
      write("hook.shell.env", { cwd: input?.cwd, sessionID: input?.sessionID, callID: input?.callID })
    },
  }
}

export default { id: "gate-recon", server: plugin }
