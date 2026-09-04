/**
 * Reads the build catalog and picks the build matching a harness version and
 * platform exactly. A different version would migrate the shared SQLite schema
 * and break the user's original install, so a near-miss is never used — the
 * caller falls back to the vanilla binary instead.
 */
import fs from "node:fs"

export type BuildEntry = { url: string; sha256: string }
export type Manifest = Record<string, Record<string, Record<string, BuildEntry>>>

export function currentPlatform(): string {
  const arch = process.arch === "arm64" ? "arm64" : process.arch === "x64" ? "x64" : process.arch
  const os = process.platform === "darwin" ? "darwin" : process.platform === "linux" ? "linux" : process.platform
  return `${os}-${arch}`
}

export function loadManifest(file: string): Manifest {
  if (!fs.existsSync(file)) return {}
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as Manifest
  } catch {
    return {}
  }
}

/** Exact (harness, tag, platform) match only. Returns null to force fallback. */
export function findBuild(
  manifest: Manifest,
  harness: string,
  tag: string,
  platform = currentPlatform(),
): BuildEntry | null {
  return manifest?.[harness]?.[tag]?.[platform] ?? null
}
