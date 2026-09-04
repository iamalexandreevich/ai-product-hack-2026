/** Which harness are we inside, and does it have the permission hook alive. */
import path from "node:path"
import type { HarnessInfo } from "../../core/src/request.ts"

export function detectHarness(env = process.env): { name: string; version: string } {
  const binary = path.basename(process.execPath).toLowerCase()
  // Kilo mirrors opencode's whole env namespace under KILO_*, so either the
  // binary name or a KILO_* variable identifies it.
  const isKilo =
    binary.includes("kilo") ||
    Object.keys(env).some((key) => key.startsWith("KILO_") && key !== "KILO_PURE")
  return { name: isKilo ? "kilo" : "opencode", version: env.GATE_HARNESS_VERSION ?? "unknown" }
}

/**
 * `patched` cannot be known at startup: the only reliable signal is the
 * permission hook actually firing, which happens on the first gated call.
 */
export class HarnessState {
  readonly name: string
  version: string
  patched = false

  constructor(detected = detectHarness()) {
    this.name = detected.name
    this.version = detected.version
  }

  markPatched(): void {
    this.patched = true
  }

  info(): HarnessInfo {
    return { name: this.name, version: this.version, patched: this.patched }
  }
}
