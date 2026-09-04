/**
 * Finds installed harnesses and where their config lives.
 *
 * `opencode` (1.x) and `opencode2` (2.0) share the ~/.config/opencode
 * directory but take different config keys, so the target is decided by the
 * binary, never by the directory. Kilo lives entirely under ~/.config/kilo
 * with a mirrored env namespace.
 */
import { execFileSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export type HarnessId = "opencode" | "kilo" | "opencode2" | "pi" | "codex" | "dsh"

export type Detected = {
  id: HarnessId
  binary: string
  version: string
  configDir: string
  configFile: string
  tuiConfigFile: string
  /** Env prefix the wrapper must use: OPENCODE_* vs KILO_*. */
  envPrefix: "OPENCODE" | "KILO"
  /**
   * Harnesses that keep everything in one directory can be gated by pointing
   * that directory elsewhere: the gated copy gets our plugin, the user's own
   * install is never touched. Names the env var that relocates it.
   */
  homeEnvVar?: "CODEX_HOME" | "PI_CODING_AGENT_DIR"
}

const HOME = os.homedir()

function which(binary: string): string | null {
  try {
    return execFileSync("which", [binary], { encoding: "utf8" }).trim() || null
  } catch {
    return null
  }
}

function version(binary: string): string {
  try {
    // First whitespace-free token that looks like a version, to skip banners.
    const raw = execFileSync(binary, ["--version"], { encoding: "utf8", timeout: 15000 })
    const match = raw.match(/\d+\.\d+\.\d+(?:[-\w.]*)?/)
    return match ? match[0] : raw.trim().split("\n").pop()!.trim()
  } catch {
    return "unknown"
  }
}

/** Prefers an existing config file; otherwise the conventional .jsonc name. */
function pickConfig(dir: string, names: string[]): string {
  for (const name of names) {
    const candidate = path.join(dir, name)
    if (fs.existsSync(candidate)) return candidate
  }
  return path.join(dir, names[names.length - 1])
}

export function detectAll(env = process.env): Detected[] {
  const found: Detected[] = []

  const opencodeBin = which("opencode")
  if (opencodeBin) {
    const dir = path.join(HOME, ".config", "opencode")
    found.push({
      id: "opencode",
      binary: opencodeBin,
      version: version("opencode"),
      configDir: dir,
      configFile: pickConfig(dir, ["opencode.json", "opencode.jsonc"]),
      tuiConfigFile: pickConfig(dir, ["tui.json", "tui.jsonc"]),
      envPrefix: "OPENCODE",
    })
  }

  const kiloBin = which("kilo")
  if (kiloBin) {
    const dir = path.join(HOME, ".config", "kilo")
    found.push({
      id: "kilo",
      binary: kiloBin,
      version: version("kilo"),
      configDir: dir,
      configFile: pickConfig(dir, ["kilo.json", "kilo.jsonc"]),
      tuiConfigFile: pickConfig(dir, ["tui.json", "tui.jsonc"]),
      envPrefix: "KILO",
    })
  }

  const opencode2Bin = which("opencode2")
  if (opencode2Bin) {
    const dir = path.join(HOME, ".config", "opencode")
    found.push({
      id: "opencode2",
      binary: opencode2Bin,
      version: version("opencode2"),
      configDir: dir,
      configFile: pickConfig(dir, ["opencode.jsonc", "opencode.json"]),
      tuiConfigFile: pickConfig(dir, ["tui.jsonc", "tui.json"]),
      envPrefix: "OPENCODE",
    })
  }

  const piBin = which("pi")
  if (piBin) {
    // Pi keeps config under ~/.pi/agent (or PI_CODING_AGENT_DIR). It has no
    // permission model of its own; the extension is registered in settings.json
    // and the mode-cycle key is freed in keybindings.json.
    const dir = env.PI_CODING_AGENT_DIR ?? path.join(HOME, ".pi", "agent")
    found.push({
      id: "pi",
      binary: piBin,
      version: version("pi"),
      configDir: dir,
      configFile: path.join(dir, "settings.json"),
      tuiConfigFile: path.join(dir, "keybindings.json"),
      envPrefix: "OPENCODE", // unused for pi; the wrapper relocates the whole dir
      homeEnvVar: "PI_CODING_AGENT_DIR",
    })
  }

  const codexBin = which("codex")
  if (codexBin) {
    // Codex loads hooks only from an installed plugin, and plugins live under
    // CODEX_HOME. Relocating that is what keeps the user's own codex clean.
    const dir = env.CODEX_HOME ?? path.join(HOME, ".codex")
    found.push({
      id: "codex",
      binary: codexBin,
      version: version("codex"),
      configDir: dir,
      configFile: path.join(dir, "config.toml"),
      tuiConfigFile: path.join(dir, "config.toml"),
      envPrefix: "OPENCODE", // unused
      homeEnvVar: "CODEX_HOME",
    })
  }

  const dshBin = which("dsh")
  if (dshBin) {
    // dsh composes its runtime from a named profile, so gating is a profile of
    // its own; the user's profiles stay as they were.
    const dir = path.join(HOME, ".dsh", "profiles")
    found.push({
      id: "dsh",
      binary: dshBin,
      version: version("dsh"),
      configDir: dir,
      configFile: path.join(dir, "gate", "cordis.patch.yml"),
      tuiConfigFile: path.join(dir, "gate", "cordis.patch.yml"),
      envPrefix: "OPENCODE", // unused
    })
  }

  return found
}

export function findVsCodeKilo(): string | null {
  const extensions = path.join(HOME, ".vscode", "extensions")
  try {
    const match = fs.readdirSync(extensions).find((name) => name.startsWith("kilocode."))
    return match ? path.join(extensions, match) : null
  } catch {
    return null
  }
}
