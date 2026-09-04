/**
 * Gated config homes.
 *
 * Codex and Pi keep everything — settings, plugins, credentials — in one
 * directory, and both let an env var relocate it. That is what makes a gated
 * copy possible without editing anything the user owns: we prepare a directory
 * of our own, register the plugin there, and the wrapper points the harness at
 * it. `codex` and `codex-gate` become two programs to run, not one program in
 * two states.
 *
 * What is copied and what is linked is not cosmetic. Files we modify (Codex's
 * `config.toml`, Pi's `settings.json`) must be copies, or installing would edit
 * the user's own. Credentials and history are linked, so a login in one is a
 * login in both. Per-home databases are deliberately neither: two binaries
 * sharing one SQLite file can migrate its schema out from under each other.
 */
import fs from "node:fs"
import path from "node:path"

export type HomeLayout = {
  /** Files we will modify — taken as copies, and only when not already there. */
  copy: string[]
  /** Shared state — symlinked, so credentials and history stay in one place. */
  link: string[]
}

export type PreparedHome = {
  dir: string
  created: boolean
  copied: string[]
  linked: string[]
}

export const CODEX_LAYOUT: HomeLayout = {
  copy: ["config.toml"],
  link: [
    "auth.json",
    "history.jsonl",
    "installation_id",
    "models_cache.json",
    "memories",
    "rules",
    "sessions",
    "skills",
    "version.json",
  ],
}

export const PI_LAYOUT: HomeLayout = {
  // keybindings.json is copied, not linked: freeing Shift+Tab for the gate must
  // not rebind the key in the user's own `pi`.
  copy: ["settings.json", "keybindings.json"],
  link: ["auth.json", "models.json", "models-store.json"],
}

/** Creates the link unless something real is already sitting there. */
export function ensureLink(linkPath: string, target: string): boolean {
  if (!fs.existsSync(target)) return false
  try {
    const existing = fs.lstatSync(linkPath)
    if (existing.isSymbolicLink() && fs.readlinkSync(linkPath) === target) return false
    // A real file or directory at that path belongs to someone else.
    if (!existing.isSymbolicLink()) return false
    fs.unlinkSync(linkPath)
  } catch {
    // Nothing there yet.
  }
  fs.mkdirSync(path.dirname(linkPath), { recursive: true })
  fs.symlinkSync(target, linkPath)
  return true
}

/**
 * Prepares the gated home. Copies are made once: `codex plugin add` writes the
 * registration into our copy of `config.toml`, and re-copying on a second
 * install would wipe it along with the hook trust state.
 */
export function prepareHome(sourceDir: string, gateDir: string, layout: HomeLayout): PreparedHome {
  const created = !fs.existsSync(gateDir)
  fs.mkdirSync(gateDir, { recursive: true })
  const copied: string[] = []
  const linked: string[] = []

  for (const name of layout.copy) {
    const from = path.join(sourceDir, name)
    const to = path.join(gateDir, name)
    if (fs.existsSync(to)) continue
    if (!fs.existsSync(from)) continue
    fs.copyFileSync(from, to)
    copied.push(to)
  }

  for (const name of layout.link) {
    const from = path.join(sourceDir, name)
    if (ensureLink(path.join(gateDir, name), from)) linked.push(path.join(gateDir, name))
  }

  return { dir: gateDir, created, copied, linked }
}

/**
 * Removes a gated home. `rmSync` unlinks symlinks instead of descending through
 * them, which is what keeps the user's real sessions and credentials safe — the
 * guard below refuses any path outside our own directory in case that ever
 * stops being true.
 */
export function removeHome(gateDir: string, insideDir: string): boolean {
  const resolved = path.resolve(gateDir)
  if (!resolved.startsWith(path.resolve(insideDir) + path.sep)) {
    throw new Error(`refusing to remove ${resolved}: outside ${insideDir}`)
  }
  if (!fs.existsSync(resolved)) return false
  fs.rmSync(resolved, { recursive: true, force: true })
  return true
}
