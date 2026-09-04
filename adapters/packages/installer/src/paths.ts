/** Where Gate keeps its own files. Overridable so tests never touch real HOME. */
import os from "node:os"
import path from "node:path"

export type GatePaths = {
  home: string
  statePath: string
  /** The deterministic ruleset every harness sends; the user may edit it. */
  rulesPath: string
  buildsDir: string
  binDir: string
  logPath: string
}

export function gatePaths(home = os.homedir()): GatePaths {
  return {
    home,
    statePath: path.join(home, ".config", "gate", "state.json"),
    rulesPath: path.join(home, ".config", "gate", "rules.json"),
    buildsDir: path.join(home, ".local", "share", "gate"),
    binDir: path.join(home, ".local", "bin"),
    logPath: path.join(home, ".local", "share", "gate", "gate.log"),
  }
}

/**
 * Where a harness's gated config directory lives. Codex and Pi keep everything
 * in one directory and let an env var relocate it; the gated copy goes here.
 */
export function harnessHomeDir(paths: GatePaths, id: string): string {
  return path.join(paths.buildsDir, "home", id)
}

/** The npm spec the harness loads. A local checkout during the hackathon; the
 *  published package name once it ships. */
export const PLUGIN_SPEC = process.env.GATE_PLUGIN_SPEC ?? "@agentgate/gate-plugin"
/** For an npm package the TUI entry is "<pkg>/tui"; for a local file path used
 *  in development it is the sibling tui.ts, since a path has no export map. */
export const PLUGIN_TUI_SPEC = PLUGIN_SPEC.includes("/src/")
  ? PLUGIN_SPEC.replace(/index\.ts$/, "tui.ts")
  : `${PLUGIN_SPEC}/tui`
export const PLUGIN_V2_SPEC = process.env.GATE_PLUGIN_V2_SPEC ?? "@agentgate/gate-plugin-v2"
export const PLUGIN_PI_SPEC = process.env.GATE_PLUGIN_PI_SPEC ?? "@agentgate/gate-plugin-pi"
