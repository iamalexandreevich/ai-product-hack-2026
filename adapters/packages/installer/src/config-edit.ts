/**
 * Idempotent edits to a harness config, comment-preserving, with a .bak taken
 * before the first change. Every function is safe to run twice.
 */
import fs from "node:fs"
import path from "node:path"
import { parseJsonc, removeTopLevelKey, setTopLevelKey } from "./jsonc.ts"

export function backup(file: string): void {
  const bak = `${file}.bak`
  if (fs.existsSync(file) && !fs.existsSync(bak)) {
    fs.copyFileSync(file, bak)
    restrict(bak)
  }
}

/**
 * The guard token is written into the harness's own config, because that is
 * where the plugin reads its options from. That file was being left at the
 * mode it already had -- 0644 on a fresh install -- so a bearer good for every
 * decision the guard makes was readable by any local account, and carried into
 * the dotfiles repositories people keep these configs in.
 *
 * Narrowing it here rather than at each call site: every path that writes a
 * config goes through this module, and a mode that depends on which function
 * you used is a mode nobody can reason about.
 */
function restrict(file: string): void {
  try {
    fs.chmodSync(file, 0o600)
  } catch {
    // Windows and some network mounts have no POSIX modes. Failing the whole
    // install over a permission we cannot express would be worse than the
    // exposure we are narrowing.
  }
}

function read(file: string): string {
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "{}\n"
}

function write(file: string, text: string): void {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, text)
  restrict(file)
}

/** Appends a value to a top-level array key, only if it is not already there. */
export function ensureInArray(file: string, key: string, value: unknown): void {
  const text = read(file)
  const config = parseJsonc(text)
  const current: unknown[] = Array.isArray(config[key]) ? config[key] : []
  const serialized = JSON.stringify(value)
  if (current.some((item) => JSON.stringify(item) === serialized)) return
  backup(file)
  write(file, setTopLevelKey(text, key, [...current, value]))
}

export function removeFromArray(file: string, key: string, predicate: (item: unknown) => boolean): void {
  if (!fs.existsSync(file)) return
  const text = read(file)
  const config = parseJsonc(text)
  const current: unknown[] = Array.isArray(config[key]) ? config[key] : []
  const next = current.filter((item) => !predicate(item))
  if (next.length === current.length) return
  backup(file)
  write(file, next.length ? setTopLevelKey(text, key, next) : removeTopLevelKey(text, key))
}

/** Merges keys into a top-level object, keeping the caller's existing entries. */
export function mergeObject(file: string, key: string, patch: Record<string, unknown>): void {
  const text = read(file)
  const config = parseJsonc(text)
  const current = (config[key] && typeof config[key] === "object" ? config[key] : {}) as Record<string, unknown>
  let changed = false
  for (const [k, v] of Object.entries(patch)) {
    if (JSON.stringify(current[k]) !== JSON.stringify(v)) changed = true
  }
  if (!changed) return
  backup(file)
  write(file, setTopLevelKey(text, key, { ...current, ...patch }))
}

export function setKey(file: string, key: string, value: unknown): void {
  const text = read(file)
  const config = parseJsonc(text)
  if (JSON.stringify(config[key]) === JSON.stringify(value)) return
  backup(file)
  write(file, setTopLevelKey(text, key, value))
}

export function removeKey(file: string, key: string): void {
  if (!fs.existsSync(file)) return
  const text = read(file)
  backup(file)
  write(file, removeTopLevelKey(text, key))
}

/**
 * On a stock binary the harness only shows a prompt when its own rules say
 * `ask`, so the fallback path needs a baseline. These are the tools worth
 * gating; the guard then suppresses the prompt for anything it allows.
 */
export const FALLBACK_PERMISSION_BASELINE: Record<string, unknown> = {
  bash: "ask",
  edit: "ask",
  write: "ask",
  webfetch: "ask",
  external_directory: "ask",
}

/** Adds only the keys the object does not already have. Never overwrites a
 *  value the user set. Returns the list of keys it actually added. */
export function fillMissing(file: string, key: string, patch: Record<string, unknown>): string[] {
  const text = read(file)
  const config = parseJsonc(text)
  const current = (config[key] && typeof config[key] === "object" ? config[key] : {}) as Record<string, unknown>
  const added: string[] = []
  const next = { ...current }
  for (const [k, v] of Object.entries(patch)) {
    if (!(k in current)) {
      next[k] = v
      added.push(k)
    }
  }
  if (!added.length) return []
  backup(file)
  write(file, setTopLevelKey(text, key, next))
  return added
}

/** Removes the named keys from a top-level object, dropping the object if it
 *  ends up empty. Used by uninstall to remove exactly what install added. */
export function removeObjectKeys(file: string, key: string, keys: string[]): void {
  if (!fs.existsSync(file) || !keys.length) return
  const text = read(file)
  const config = parseJsonc(text)
  const current = (config[key] && typeof config[key] === "object" ? config[key] : {}) as Record<string, unknown>
  let changed = false
  const next: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(current)) {
    if (keys.includes(k)) changed = true
    else next[k] = v
  }
  if (!changed) return
  backup(file)
  write(file, Object.keys(next).length ? setTopLevelKey(text, key, next) : removeTopLevelKey(text, key))
}
