/**
 * The gate mode lives in a file because the two halves of the plugin cannot
 * talk to each other: the server plugin and the TUI plugin are separate
 * modules (and separate processes in opencode 2.0). A file is the only channel
 * that works in every harness without patching one.
 */
import fs from "node:fs"
import path from "node:path"
import type { Mode } from "./config.ts"

export type GateState = { mode: Mode }

const ORDER: Mode[] = ["auto", "ask", "allow"]
const VALID: Mode[] = ["auto", "ask", "allow", "off"]

export function isMode(value: unknown): value is Mode {
  return typeof value === "string" && (VALID as string[]).includes(value)
}

/** Shift+Tab cycles the three working modes. `off` is deliberately not in the
 *  cycle: turning protection off should take an explicit command. */
export function cycle(mode: Mode): Mode {
  const at = ORDER.indexOf(mode)
  return at === -1 ? ORDER[0] : ORDER[(at + 1) % ORDER.length]
}

type Cached = { mtimeMs: number; mode: Mode }
const cache = new Map<string, Cached>()

/** Read is on the hot path — every decision calls it — so it is stat-cached. */
export function readMode(statePath: string): Mode {
  try {
    const { mtimeMs } = fs.statSync(statePath)
    const hit = cache.get(statePath)
    if (hit && hit.mtimeMs === mtimeMs) return hit.mode
    const parsed = JSON.parse(fs.readFileSync(statePath, "utf8")) as Partial<GateState>
    const mode = isMode(parsed?.mode) ? parsed.mode : "auto"
    cache.set(statePath, { mtimeMs, mode })
    return mode
  } catch {
    // No state file yet, or unreadable: protection on by default.
    return "auto"
  }
}

/** Written atomically so a reader never sees a half-written file. */
export function writeMode(statePath: string, mode: Mode): void {
  fs.mkdirSync(path.dirname(statePath), { recursive: true })
  const tmp = `${statePath}.${process.pid}.tmp`
  fs.writeFileSync(tmp, `${JSON.stringify({ mode }, null, 2)}\n`)
  fs.renameSync(tmp, statePath)
  cache.delete(statePath)
}

export function clearModeCache(): void {
  cache.clear()
}
