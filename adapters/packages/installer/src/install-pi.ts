/**
 * Pi: gated by a config directory of our own.
 *
 * Pi loads extensions from `settings.json` in the directory named by
 * PI_CODING_AGENT_DIR. Registering the extension in the user's own directory
 * would gate their everyday `pi` too, so the installer prepares a copy and the
 * `pi-gate` wrapper points Pi at it. Install and uninstall live together here
 * so the two halves cannot drift apart.
 */
import fs from "node:fs"
import path from "node:path"
import { piExtensionPath } from "./bundle.ts"
import { ensureInArray, removeKey, removeFromArray, setKey } from "./config-edit.ts"
import type { Detected } from "./detect.ts"
import { PI_LAYOUT, prepareHome, removeHome } from "./home.ts"
import { parseJsonc } from "./jsonc.ts"
import { harnessHomeDir, type GatePaths } from "./paths.ts"
import { removeWrapper, writeHomeWrapper } from "./wrapper.ts"

export type PiInstallOptions = {
  guardUrl: string
  token?: string
  profileId?: string
  log: (line: string) => void
}

export type PiRecord = { home: string; wrapper: string }

/** Pi binds Shift+Tab to its own thinking-mode cycle; move it aside for the gate. */
function freeThinkingCycle(file: string): boolean {
  try {
    const current = fs.existsSync(file) ? (parseJsonc(fs.readFileSync(file, "utf8")) as Record<string, unknown>) : {}
    if (current["app.thinking.cycle"] !== undefined) return false
    setKey(file, "app.thinking.cycle", "ctrl+shift+t")
    return true
  } catch {
    return false
  }
}

export function installPi(target: Detected, paths: GatePaths, options: PiInstallOptions): PiRecord {
  const home = harnessHomeDir(paths, "pi")
  if (path.resolve(target.configDir) === path.resolve(home)) {
    throw new Error("refusing to install into gate's own Pi home — run this outside `pi-gate`")
  }

  prepareHome(target.configDir, home, PI_LAYOUT)
  const extension = process.env.GATE_PI_EXTENSION ?? piExtensionPath()
  if (!extension) {
    options.log("  ! Pi extension source not found — is this a full checkout?")
  } else {
    // Absolute path: Pi resolves the extension's own relative imports of the
    // shared core from the file's location.
    ensureInArray(path.join(home, "settings.json"), "extensions", extension)
  }
  freeThinkingCycle(path.join(home, "keybindings.json"))

  const wrapper = writeHomeWrapper("pi", {
    binDir: paths.binDir,
    homeEnvVar: "PI_CODING_AGENT_DIR",
    homeDir: home,
    guardUrl: options.guardUrl,
    token: options.token,
    profileId: options.profileId,
  })
  return { home, wrapper }
}

export function uninstallPi(target: Detected, paths: GatePaths, log: (line: string) => void): void {
  removeWrapper(paths.binDir, target.id)
  removeHome(harnessHomeDir(paths, "pi"), paths.buildsDir)

  // Earlier versions registered the extension in the user's own settings.json.
  // Clean that up on upgrade, or it keeps gating their everyday `pi` forever.
  const legacy = fs.existsSync(target.configFile)
    ? fs.readFileSync(target.configFile, "utf8")
    : ""
  if (legacy.includes("plugin-pi") || legacy.includes("gate-plugin-pi")) {
    removeFromArray(target.configFile, "extensions", (item) =>
      typeof item === "string" && (item.includes("plugin-pi") || item.includes("gate-plugin-pi")),
    )
    removeKey(target.tuiConfigFile, "app.thinking.cycle")
    log("  removed a legacy gate registration from your own Pi config")
  }
}
