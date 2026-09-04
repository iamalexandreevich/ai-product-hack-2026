/**
 * Self-contained plugin bundles.
 *
 * The harness resolves a `plugin[]` entry either as an npm spec or as a path to
 * a file it can import. `@agentgate/gate-plugin` is not published, so the
 * installer builds the plugin into one file under Gate's own directory and
 * points the wrapper at that path. Two consequences worth keeping: the user's
 * config never gains an unresolvable package name, and the bundle lives outside
 * the harness's plugin directory, so the untouched binary keeps behaving exactly
 * as before Gate was installed.
 */
import { execFileSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import type { GatePaths } from "./paths.ts"

export type BundleSet = { server: string; tui: string }

/** Repo root (adapters/), derived from this file's own location. */
export function adaptersRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..")
}

function haveBun(): boolean {
  try {
    execFileSync("bun", ["--version"], { stdio: "ignore" })
    return true
  } catch {
    return false
  }
}

/**
 * Builds the v1 server and TUI plugins into `<gate>/bundles`. Returns null when
 * the sources or bun are missing, which the caller reports rather than writing a
 * config entry that would silently fail to load.
 */
export function buildBundles(paths: GatePaths): BundleSet | null {
  const root = adaptersRoot()
  const entries = {
    server: path.join(root, "packages", "plugin-v1", "src", "index.ts"),
    tui: path.join(root, "packages", "plugin-v1", "src", "tui.ts"),
  }
  if (!fs.existsSync(entries.server)) return null
  if (!haveBun()) return null

  const outDir = path.join(paths.buildsDir, "bundles")
  fs.mkdirSync(outDir, { recursive: true })
  const out: BundleSet = { server: path.join(outDir, "gate.js"), tui: path.join(outDir, "gate-tui.js") }

  for (const [key, entry] of Object.entries(entries)) {
    const target = out[key as keyof BundleSet]
    if (!fs.existsSync(entry)) continue
    execFileSync("bun", ["build", entry, "--target=node", "--format=esm", "--outfile", target], {
      stdio: "ignore",
    })
  }
  return out
}

/** Pi loads a .ts extension straight from disk and resolves the file's own
 *  relative imports, so it needs the source path rather than a bundle. */
export function piExtensionPath(): string | null {
  const file = path.join(adaptersRoot(), "packages", "plugin-pi", "src", "index.ts")
  return fs.existsSync(file) ? file : null
}

export function codexMarketplacePath(root?: string): string {
  root = root ?? adaptersRoot()
  return path.join(root, "packages", "plugin-codex", "marketplace")
}

/**
 * Copies the shared core into the Codex plugin. Codex copies a plugin into its
 * own cache, so the plugin must be self-contained — and the vendored tree is
 * gitignored, so on a fresh clone this is the installer's job, not a leftover.
 */
export function vendorCodexCore(root?: string): string | null {
  root = root ?? adaptersRoot()
  const from = path.join(root, "packages", "core", "src")
  if (!fs.existsSync(from)) return null
  const to = path.join(codexMarketplacePath(root), "plugins", "gate", "vendor", "core")
  fs.rmSync(to, { recursive: true, force: true })
  fs.mkdirSync(to, { recursive: true })
  for (const name of fs.readdirSync(from)) {
    if (name.endsWith(".ts")) fs.copyFileSync(path.join(from, name), path.join(to, name))
  }
  return to
}

export function removeBundles(paths: GatePaths): void {
  fs.rmSync(path.join(paths.buildsDir, "bundles"), { recursive: true, force: true })
}
