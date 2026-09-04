/** Builds wire requests from a mapped harness action. */
import type { MappedAction } from "./mapping.ts"
import { clampHarness, clampMetadata, clampRawBytes, clampSessionId, clampUserRequest } from "./limits.ts"
import type { DecideRequest, InspectRequest, Provenance } from "./protocol.ts"

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
  model?: string
  agentId?: string
  /** Set when the call happens inside a `task` subagent session. */
  parentSessionId?: string | null
}

/**
 * Everything the closed `tool` enum cannot carry rides in metadata. The service
 * stores and echoes metadata but does not reason about it, so this is
 * bookkeeping for logs and the benchmark until the contract grows real fields
 * (see docs/contract-gaps.md, items 3, 4, 6, 7).
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
