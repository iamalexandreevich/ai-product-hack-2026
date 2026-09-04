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
  return {
    harness: HARNESS,
    sessionId: event.session_id ?? null,
    callId: event.tool_use_id ?? event.turn_id ?? "unknown",
    userRequest: "",
    mode: mode(),
    profileId: config.profileId,
    model: event.model ?? config.model,
  }
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
