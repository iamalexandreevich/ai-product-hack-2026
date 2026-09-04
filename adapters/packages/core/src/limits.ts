/**
 * Request limits from openapi.yaml. Two of them are counted in BYTES, not
 * characters, which matters for Cyrillic: 20000 Russian characters is already
 * over the 32 KB `raw` limit.
 *
 * The service answers `ask` rather than an error when a limit is exceeded, so
 * clamping here is not about correctness — it is about not turning a long file
 * into a pointless confirmation prompt.
 */
import { LIMITS } from "./protocol.ts"

const encoder = new TextEncoder()

export function byteLength(value: string): number {
  return encoder.encode(value).length
}

/** Keeps the head: for a command, the beginning is what identifies it. */
export function clampRawBytes(raw: string, limit: number = LIMITS.rawBytes): string {
  if (byteLength(raw) <= limit) return raw
  const marker = "…[gate: truncated]"
  const budget = limit - byteLength(marker)
  let out = raw
  while (byteLength(out) > budget) out = out.slice(0, Math.floor(out.length * 0.9))
  return out + marker
}

/** Keeps the tail: the end of the message describes the current task. */
export function clampUserRequest(value: string, limit: number = LIMITS.userRequestChars): string {
  return value.length <= limit ? value : value.slice(-limit)
}

export function clampSessionId(value: string | null | undefined): string | null {
  if (!value) return null
  return value.length <= LIMITS.sessionIdChars ? value : value.slice(0, LIMITS.sessionIdChars)
}

export function clampHarness(value: string): string {
  const trimmed = value.trim() || "unknown"
  return trimmed.slice(0, LIMITS.harnessChars)
}

/** Drops keys from the end until the serialized object fits. */
export function clampMetadata(
  metadata: Record<string, unknown>,
  limit: number = LIMITS.metadataBytes,
): Record<string, unknown> {
  if (byteLength(JSON.stringify(metadata)) <= limit) return metadata
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(metadata)) {
    const candidate = { ...out, [key]: value }
    if (byteLength(JSON.stringify(candidate)) > limit) break
    out[key] = value
  }
  return out
}
