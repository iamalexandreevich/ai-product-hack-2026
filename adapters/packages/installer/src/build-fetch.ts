/**
 * Places the patched build for a target at the expected path, fetching it from
 * the manifest if it is not already there. Supports file:// (local catalog,
 * used in development and on the demo machine) and http(s):// (GitHub Releases).
 * The sha256 is always verified — a build of the wrong version would migrate
 * the shared SQLite schema and break the user's original install.
 */
import { createHash } from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import { findBuild, loadManifest, type BuildEntry } from "./manifest.ts"

function sha256(file: string): string {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex")
}

async function download(entry: BuildEntry, dest: string): Promise<void> {
  if (entry.url.startsWith("file://")) {
    fs.copyFileSync(entry.url.slice("file://".length), dest)
    return
  }
  const res = await fetch(entry.url)
  if (!res.ok) throw new Error(`fetch ${entry.url} -> HTTP ${res.status}`)
  fs.writeFileSync(dest, Buffer.from(await res.arrayBuffer()))
}

/**
 * Ensures the build is present and correct. Returns the binary path, or null
 * if no matching build exists (the caller then falls back to the vanilla
 * binary). Throws only on a real mismatch — a corrupt or wrong-version build.
 */
export async function ensureBuild(
  manifestFile: string,
  harness: string,
  tag: string,
  destBinary: string,
): Promise<string | null> {
  const entry = findBuild(loadManifest(manifestFile), harness, tag)
  if (!entry) return null

  if (fs.existsSync(destBinary) && sha256(destBinary) === entry.sha256) return destBinary

  fs.mkdirSync(path.dirname(destBinary), { recursive: true })
  await download(entry, destBinary)
  const actual = sha256(destBinary)
  if (actual !== entry.sha256) {
    fs.rmSync(destBinary, { force: true })
    throw new Error(`sha256 mismatch for ${harness} ${tag}: expected ${entry.sha256}, got ${actual}`)
  }
  fs.chmodSync(destBinary, 0o755)
  return destBinary
}
