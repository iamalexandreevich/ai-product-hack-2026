/**
 * Turns a detected harness into the concrete edits install/uninstall perform.
 * Pure: it computes what to do so the commands and the tests agree on it.
 */
import type { Detected } from "./detect.ts"
import { findBuild, loadManifest } from "./manifest.ts"
import { PLUGIN_PI_SPEC, PLUGIN_SPEC, PLUGIN_TUI_SPEC, PLUGIN_V2_SPEC, type GatePaths } from "./paths.ts"
import { wrapperPath } from "./wrapper.ts"

export type InstallMode = "patched" | "fallback" | "v2" | "pi" | "codex" | "dsh"

export type InstallPlan = {
  target: Detected
  mode: InstallMode
  /** Present when a matching patched build exists; the wrapper execs it. */
  buildBinary: string | null
  wrapper: string | null
  pluginSpec: string
  tuiSpec: string
  configKey: "plugin" | "plugins"
  /** Only in fallback: the harness needs baseline ask-rules to show prompts. */
  needsPermissionBaseline: boolean
  restartHint: string
}

export function planInstall(
  target: Detected,
  paths: GatePaths,
  manifestFile: string,
): InstallPlan {
  if (target.id === "codex") {
    // No patch: Codex's own hooks are enough. The gated copy lives in its own
    // CODEX_HOME so the user's `codex` keeps running without our plugin.
    return {
      target,
      mode: "codex",
      buildBinary: null,
      wrapper: wrapperPath(paths.binDir, target.id),
      pluginSpec: "gate@agentgate",
      tuiSpec: "gate@agentgate",
      configKey: "plugin",
      needsPermissionBaseline: false,
      restartHint: `run: ${target.id}-gate`,
    }
  }

  if (target.id === "dsh") {
    // No patch: the harness exposes exactly the two seams we need. Gating is a
    // profile of its own, so the user's profiles are untouched.
    return {
      target,
      mode: "dsh",
      buildBinary: null,
      wrapper: wrapperPath(paths.binDir, target.id),
      pluginSpec: "@agentgate/dsh-gate",
      tuiSpec: "@agentgate/dsh-gate",
      configKey: "plugin",
      needsPermissionBaseline: false,
      restartHint: `run: ${target.id}-gate`,
    }
  }

  if (target.id === "pi") {
    // No patch. The extension is registered in a settings.json of our own,
    // reached through PI_CODING_AGENT_DIR, so plain `pi` stays ungated.
    return {
      target,
      mode: "pi",
      buildBinary: null,
      wrapper: wrapperPath(paths.binDir, target.id),
      pluginSpec: PLUGIN_PI_SPEC,
      tuiSpec: PLUGIN_PI_SPEC,
      configKey: "plugin",
      needsPermissionBaseline: false,
      restartHint: `run: ${target.id}-gate`,
    }
  }

  if (target.id === "opencode2") {
    return {
      target,
      mode: "v2",
      buildBinary: null,
      wrapper: null,
      pluginSpec: PLUGIN_V2_SPEC,
      tuiSpec: `${PLUGIN_V2_SPEC}/tui`,
      configKey: "plugins",
      needsPermissionBaseline: false,
      restartHint: "opencode2 service restart",
    }
  }

  const manifest = loadManifest(manifestFile)
  const build = findBuild(manifest, target.id, target.version)
  const patched = build !== null

  return {
    target,
    mode: patched ? "patched" : "fallback",
    buildBinary: patched
      ? `${paths.buildsDir}/${target.id}/${target.version}/${target.id}`
      : null,
    // A wrapper in both modes. Without one, `fallback` gated the user's own
    // `kilo` by editing their config -- the opposite of the rule this installer
    // lives by, which is that the original command keeps working untouched and
    // the gated one gets a name of its own.
    wrapper: wrapperPath(paths.binDir, target.id),
    pluginSpec: PLUGIN_SPEC,
    tuiSpec: PLUGIN_TUI_SPEC,
    configKey: "plugin",
    needsPermissionBaseline: !patched,
    restartHint: `restart the harness, then run: ${target.id}-gate`,
  }
}
