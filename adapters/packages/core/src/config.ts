/** Everything the adapter is allowed to know: a URL, a token, maybe a profile. */
import os from "node:os"
import path from "node:path"

export type Mode = "auto" | "ask" | "allow" | "off"

/** What to do when the guard cannot be reached at all (no answer, not a refusal). */
export type OnUnavailable = "allow" | "ask" | "deny"

export type GateConfig = {
  url: string
  token?: string
  profileId?: string
  model?: string
  decidePath: string
  /** Provisional: the post-tool-use endpoint does not exist server-side yet. */
  inspectPath: string
  healthPath: string
  decideTimeoutMs: number
  inspectTimeoutMs: number
  onUnavailable: OnUnavailable
  statePath: string
  logPath: string
}

const DEFAULTS: GateConfig = {
  url: "http://127.0.0.1:8400",
  decidePath: "/v1/decide",
  inspectPath: "/v1/inspect",
  healthPath: "/healthz",
  decideTimeoutMs: 10_000,
  inspectTimeoutMs: 15_000,
  onUnavailable: "allow",
  statePath: path.join(os.homedir(), ".config", "gate", "state.json"),
  logPath: path.join(os.homedir(), ".local", "share", "gate", "gate.log"),
}

function num(value: string | undefined, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function defined<T extends object>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as Partial<T>
}

/**
 * Precedence: explicit plugin options beat environment, environment beats
 * defaults. Plugin options come from the harness config, which is what the
 * installer writes, so they are the most specific thing available.
 */
export function loadConfig(options: Partial<GateConfig> = {}, env = process.env): GateConfig {
  const fromEnv = defined({
    url: env.AGENTGATE_URL,
    token: env.AGENTGATE_TOKEN,
    profileId: env.AGENTGATE_PROFILE,
    model: env.AGENTGATE_MODEL,
    inspectPath: env.GATE_INSPECT_PATH,
    statePath: env.GATE_STATE_PATH,
    logPath: env.GATE_LOG_PATH,
  })

  const merged: GateConfig = {
    ...DEFAULTS,
    ...fromEnv,
    decideTimeoutMs: num(env.GATE_DECIDE_TIMEOUT_MS, DEFAULTS.decideTimeoutMs),
    inspectTimeoutMs: num(env.GATE_INSPECT_TIMEOUT_MS, DEFAULTS.inspectTimeoutMs),
    // GATE_FAIL_CLOSED is the spec's blunt switch; on_unavailable is the precise one.
    onUnavailable: isOnUnavailable(env.GATE_ON_UNAVAILABLE)
      ? env.GATE_ON_UNAVAILABLE
      : env.GATE_FAIL_CLOSED === "1"
        ? "deny"
        : DEFAULTS.onUnavailable,
    ...defined(options),
  }

  merged.url = merged.url.replace(/\/+$/, "")
  return merged
}

function isOnUnavailable(value: string | undefined): value is OnUnavailable {
  return value === "allow" || value === "ask" || value === "deny"
}

export function endpoint(config: GateConfig, which: "decide" | "inspect" | "health"): string {
  const suffix =
    which === "decide" ? config.decidePath : which === "inspect" ? config.inspectPath : config.healthPath
  return `${config.url}${suffix}`
}
