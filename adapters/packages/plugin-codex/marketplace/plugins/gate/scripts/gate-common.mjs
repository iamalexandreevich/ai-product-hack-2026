/** Shared plumbing for the Codex hook scripts.
 * Codex runs each hook as a bare `node <script>` subprocess and copies the
 * plugin into its own cache, so core is vendored alongside (see sync-core.sh)
 * rather than resolved from the workspace. */
import { readFileSync } from "node:fs"
import {
  GuardClient,
  buildDecideRequest,
  buildInspectRequest,
  createLogger,
  idempotencyKey,
  loadConfig,
  readRules,
  clampHistory,
  mapToolCall,
  readMode,
} from "../vendor/core/index.ts"

export { buildDecideRequest, buildInspectRequest, idempotencyKey, mapToolCall }

export const config = loadConfig()
export const log = createLogger(config.logPath)
export const client = new GuardClient(config, log)
export const mode = () => readMode(config.statePath)

/** The harness identity carried on every request. Codex needs no patch. */
export const HARNESS = { name: "codex", version: process.env.CODEX_VERSION ?? "unknown", patched: false }

export function readEvent() {
  try { return JSON.parse(readFileSync(0, "utf8")) } catch { return {} }
}

export function contextFor(event) {
  const turns = historyOf(event)
  return {
    harness: HARNESS,
    sessionId: event.session_id ?? null,
    callId: event.tool_use_id ?? event.turn_id ?? "unknown",
    userRequest: turns.length ? lastHumanOf(turns) : "",
    history: turns,
    mode: mode(),
    rules: readRules(config.rulesPath),
    profileId: config.profileId,
    // `model` selects a classifier config inside the guard's profile — it is
    // NOT the coding agent's model. Sending Codex's own model here makes the
    // service refuse the request with `api.unknown-model`, fail-closed to
    // `ask`, and drop the decision. The agent's model belongs in metadata.
    model: config.model,
    agentModel: event.model ?? null,
  }
}

/**
 * Codex's hook event carries no conversation, but it does hand us the path to
 * the session transcript — which makes it the one harness where the full
 * history is available without keeping any state: every hook is its own
 * process, so nothing survives between calls.
 *
 * An unreadable or malformed transcript degrades to an empty history rather
 * than failing the call; the guard then judges on the action alone, exactly as
 * it did before history existed.
 */
function historyOf(event) {
  const file = event.transcript_path
  if (!file) return []
  let lines
  try { lines = readFileSync(file, "utf8").split("\n") } catch { return [] }

  const turns = []
  for (const line of lines) {
    if (!line.trim()) continue
    let entry
    try { entry = JSON.parse(line) } catch { continue }
    const turn = turnOf(entry?.payload ?? entry)
    if (turn) turns.push(turn)
  }
  return clampHistory(turns)
}

/** The transcript is JSONL of mixed shapes; anything we do not recognise is
 * skipped rather than guessed at, since a mislabelled turn is worse for the
 * classifier than a missing one. */
function turnOf(payload) {
  if (!payload || typeof payload !== "object") return null

  if (payload.type === "function_call" || payload.type === "local_shell_call") {
    const content = textOf(payload.arguments ?? payload.action ?? payload.input)
    return content
      ? { role: "toolcall", author: "agent", content, ...named(payload.name), ...called(payload.call_id) }
      : null
  }
  if (payload.type === "function_call_output" || payload.type === "local_shell_call_output") {
    const content = textOf(payload.output ?? payload.result)
    return content
      ? { role: "toolresult", author: "system", content, ...named(payload.name), ...called(payload.call_id) }
      : null
  }

  const role = payload.role ?? (payload.type === "user_message" ? "user" : null)
  const content = textOf(payload.content ?? payload.message ?? payload.text)
  if (!content) return null
  if (role === "user") return { role: "human", author: "human", content }
  if (role === "assistant") return { role: "assistant", author: "agent", content }
  return null
}

const named = (name) => (typeof name === "string" && name ? { tool: name.slice(0, 64) } : {})
const called = (id) => (typeof id === "string" && id ? { call_id: id.slice(0, 128) } : {})

/** Content is a string, a list of typed parts, or a structured object. */
function textOf(content) {
  if (typeof content === "string") return content.trim()
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : (part?.text ?? part?.content ?? "")))
      .filter((part) => typeof part === "string")
      .join(" ")
      .trim()
  }
  if (content && typeof content === "object") {
    try { return JSON.stringify(content) } catch { return "" }
  }
  return ""
}

const lastHumanOf = (turns) => {
  for (let i = turns.length - 1; i >= 0; i--) {
    if (turns[i].role === "human" && turns[i].author === "human") return turns[i].content
  }
  return ""
}

/** Codex spells its built-ins in PascalCase (`Bash`, `Read`, `WebFetch`) while
 * core's mapper keys off the lowercase opencode/Kilo names. Anything unknown is
 * passed through untouched so MCP tools (`server_tool`) still split correctly. */
const CODEX_TOOL_NAMES = new Map([
  ["bash", "bash"], ["shell", "bash"], ["read", "read"], ["write", "write"],
  ["edit", "edit"], ["applypatch", "patch"], ["apply_patch", "patch"],
  ["glob", "glob"], ["grep", "grep"], ["list", "list"], ["ls", "ls"],
  ["webfetch", "webfetch"], ["websearch", "websearch"], ["task", "task"],
])

export function normalizeToolName(name) {
  const raw = String(name ?? "")
  return CODEX_TOOL_NAMES.get(raw.toLowerCase()) ?? raw
}

export const actionFor = (event) =>
  mapToolCall(normalizeToolName(event.tool_name), event.tool_input ?? {}, event.cwd ?? process.cwd())

export const emit = (value) => process.stdout.write(JSON.stringify(value ?? {}))
