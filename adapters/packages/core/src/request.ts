/** Builds wire requests from a mapped harness action. */
import type { MappedAction } from "./mapping.ts"
import { clampHarness, clampMetadata, clampRawBytes, clampSessionId, clampUserRequest } from "./limits.ts"
import type { Turn } from "./history.ts"
import type { DecideRequest, InspectRequest, Provenance, RulePayload } from "./protocol.ts"

export type HarnessInfo = {
  name: string
  version: string
  /** True when running the patched build, where the permission hook is live. */
  patched: boolean
}

export type CallContext = {
  harness: HarnessInfo
  sessionId: string | null
  callId: string
  userRequest: string
  mode: string
  profileId?: string
  /**
   * Name of a classifier configuration inside the guard's profile — not the
   * coding agent's model. Sending the agent's model here is refused by the
   * service as `api.unknown-model`.
   */
  model?: string
  /** The coding agent's own model, recorded for logs only. */
  agentModel?: string | null
  agentId?: string
  /** Set when the call happens inside a `task` subagent session. */
  parentSessionId?: string | null
  /** Deterministic ruleset, carried with the action so stage 1 can apply it. */
  rules?: RulePayload | null
  /** Preceding turns, oldest first. */
  history?: Turn[] | null
}

/**
 * Everything the closed `tool` enum cannot carry rides in metadata. The service
 * stores and echoes metadata but does not reason about it, so this is
 * bookkeeping for logs and the benchmark until the contract grows real fields
 * (see docs/inspect-openapi.yaml, "Other gaps").
 */
function gateMetadata(action: MappedAction, context: CallContext): Record<string, unknown> {
  return clampMetadata({
    gate_version: "0.1.0",
    call_id: context.callId,
    tool_name: action.toolName,
    provenance: action.provenance,
    mode: context.mode,
    harness_version: context.harness.version,
    patched: context.harness.patched,
    agent_id: context.agentId ?? null,
    agent_model: context.agentModel ?? null,
    parent_session_id: context.parentSessionId ?? null,
  })
}

export function buildDecideRequest(action: MappedAction, context: CallContext): DecideRequest {
  return {
    session_id: clampSessionId(context.sessionId),
    harness: clampHarness(context.harness.name),
    tool: action.tool,
    raw: clampRawBytes(action.raw ?? ""),
    args: action.args,
    user_request: clampUserRequest(context.userRequest),
    profile_id: context.profileId ?? null,
    model: context.model ?? null,
    metadata: gateMetadata(action, context),
    ...(context.rules ? { rules: context.rules } : {}),
    // Omitted entirely when empty: an absent field and an empty list mean the
    // same to the service, and the shorter body is the honest one.
    ...(context.history?.length ? { history: context.history, protocol: 1 } : {}),
  }
}

export function buildInspectRequest(
  action: MappedAction,
  context: CallContext,
  result: { status: "completed" | "error"; output: string; provenance?: Provenance },
): InspectRequest {
  return {
    session_id: clampSessionId(context.sessionId),
    harness: clampHarness(context.harness.name),
    call_id: context.callId,
    tool: action.tool,
    tool_name: action.toolName,
    status: result.status,
    output: clampRawBytes(result.output ?? ""),
    provenance: result.provenance ?? action.provenance,
    args: action.args,
    user_request: clampUserRequest(context.userRequest),
    profile_id: context.profileId ?? null,
    metadata: gateMetadata(action, context),
  }
}
