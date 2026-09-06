/**
 * Wire types for the guard service.
 *
 * `Decide*` mirrors `POST /v1/decide` in openapi.yaml exactly — that endpoint is
 * the settled contract and must not be improvised on.
 *
 * `Inspect*` covers the post-tool-use direction, which the service does not
 * expose yet. It is modelled on `Decide*` deliberately: when the guard author
 * ships the real endpoint, only the field names here and the path in config.ts
 * should need to move. Gaps are catalogued in docs/inspect-openapi.yaml.
 */
import type { Turn } from "./history.ts"

/** Kind of action the gate reasons about. Closed enum, from openapi.yaml. */
export type Tool = "shell" | "file_write" | "file_read" | "network" | "mcp_call"

export type DecisionKind = "allow" | "deny" | "ask"

export type McpArgs = {
  server: string
  tool: string
  arguments?: Record<string, unknown>
}

export type ActionArgs = {
  /** Absolute working directory. Required and non-empty. */
  cwd: string
  /** For file_read / file_write. Ignored by the service for shell. */
  paths?: string[]
  /** For network. Ignored by the service for shell. */
  domains?: string[]
  mcp?: McpArgs | null
}

/**
 * The deterministic ruleset, sent with the action.
 *
 * The contract keeps policy on the server and has no field for this yet; it is
 * agreed as an addition. Until the service reads it the field is simply ignored,
 * which costs nothing and lets both sides land independently.
 */
export type RulePayload = {
  version: 1
  level: string
  allow: string[]
  ask: string[]
  deny: string[]
}

export type DecideRequest = {
  session_id?: string | null
  call_id?: string | null
  harness: string
  tool: Tool
  /** Required and non-blank for tool=shell: stage 1 parses it. */
  raw?: string
  args: ActionArgs
  user_request: string
  profile_id?: string | null
  model?: string | null
  metadata?: Record<string, unknown>
  /**
   * Deterministic ruleset. Not in the contract yet — agreed as an addition, and
   * ignored by a service that does not know it, so both sides can land apart.
   */
  rules?: RulePayload
  /**
   * Preceding turns, oldest first. Lets the service see intent and catch a
   * multi-step attack whose individual steps are each innocuous.
   */
  history?: Turn[]
  /** Protocol version. Only `1` exists. */
  protocol?: number
}

export type LatencyMs = {
  stage1: number | null
  stage2: number | null
  total: number
}

export type DecideResponse = {
  decision: DecisionKind
  reason: string
  suggest: string
  stage: number
  rule_id: string | null
  model: string | null
  latency_ms: LatencyMs
  cached: boolean
  decision_id: string
}

/** Where a tool result came from. Decides how much the text can be trusted. */
export type Provenance =
  | { kind: "file"; path: string }
  | { kind: "shell"; command: string }
  | { kind: "web"; url: string }
  | { kind: "mcp"; server: string; tool: string }
  | { kind: "subagent"; session_id: string }
  | { kind: "unknown" }

export type InspectVerdict = "pass" | "mask" | "drop"

/** PROVISIONAL — shaped after DecideRequest until the service defines its own. */
export type InspectRequest = {
  session_id?: string | null
  history?: Turn[]
  protocol?: number
  harness: string
  /** Ties this result back to the /v1/decide call for the same tool invocation. */
  call_id: string
  tool: Tool
  /** Harness-native tool name, which the closed `tool` enum cannot express. */
  tool_name: string
  status: "completed" | "error"
  output: string
  provenance: Provenance
  args: ActionArgs
  user_request: string
  profile_id?: string | null
  metadata?: Record<string, unknown>
}

/** PROVISIONAL — mirrors DecideResponse; `output` carries the rewrite on mask. */
export type InspectResponse = {
  verdict: InspectVerdict
  /** Replacement text. Present and authoritative when verdict is "mask". */
  output?: string
  reason: string
  suggest?: string
  stage: number
  rule_id: string | null
  model: string | null
  latency_ms: LatencyMs
  cached: boolean
  decision_id: string
}

/** Limits from openapi.yaml. `raw` and `metadata` are counted in BYTES. */
export const LIMITS = {
  sessionIdChars: 128,
  harnessChars: 64,
  rawBytes: 32768,
  metadataBytes: 16384,
  userRequestChars: 2048,
} as const
