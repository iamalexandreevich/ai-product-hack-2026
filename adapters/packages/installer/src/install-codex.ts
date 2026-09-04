/**
 * Codex: gated by a CODEX_HOME of its own.
 *
 * Codex loads hooks only from an installed plugin, and plugins live under
 * CODEX_HOME. So the one install step that cannot be a file write is
 * registering the plugin — and because CODEX_HOME points at our copy while we
 * do it, shelling out to the user's own `codex` binary is safe: every write
 * lands in our directory.
 */
import fs from "node:fs"
import path from "node:path"
import { codexMarketplacePath, vendorCodexCore } from "./bundle.ts"
import type { Detected } from "./detect.ts"
import { CODEX_LAYOUT, prepareHome, removeHome } from "./home.ts"
import type { Runner } from "./harness-cli.ts"
import { harnessHomeDir, type GatePaths } from "./paths.ts"
import { removeWrapper, writeHomeWrapper } from "./wrapper.ts"

export type CodexInstallOptions = {
  guardUrl: string
  token?: string
  profileId?: string
  run: Runner
  log: (line: string) => void
  /**
   * The checkout the plugin is taken from. Overridable because installing
   * re-vendors the shared core inside the marketplace tree, and tests must not
   * rewrite it while other suites are reading it.
   */
  root?: string
}

export type CodexRecord = { home: string; wrapper: string; marketplace: string; plugin: string }

export const MARKETPLACE = "agentgate"
export const PLUGIN = "gate@agentgate"

/** The three hooks that must be trusted before Codex will run them. */
const TRUSTED_HOOKS = ["pre_tool_use", "permission_request", "post_tool_use"]

/**
 * Trust hashes are computed over the handler config, so a `config.toml` copied
 * after the user trusted once inherits them. We can verify, never create.
 */
export function hooksTrusted(home: string): boolean {
  const file = path.join(home, "config.toml")
  if (!fs.existsSync(file)) return false
  const text = fs.readFileSync(file, "utf8")
  return TRUSTED_HOOKS.every((hook) => text.includes(`${PLUGIN}:hooks/hooks.json:${hook}`))
}

export function installCodex(
  target: Detected,
  paths: GatePaths,
  options: CodexInstallOptions,
): CodexRecord | null {
  const home = harnessHomeDir(paths, "codex")
  if (path.resolve(target.configDir) === path.resolve(home)) {
    throw new Error("refusing to install into gate's own Codex home — run this outside `codex-gate`")
  }

  // Codex copies a plugin into its own cache, so the plugin has to carry the
  // shared core with it rather than resolving it from the workspace.
  if (!vendorCodexCore(options.root)) {
    options.log("  ! could not vendor core into the Codex plugin — is this a full checkout?")
    return null
  }
  prepareHome(target.configDir, home, CODEX_LAYOUT)

  const env = { ...process.env, CODEX_HOME: home }
  const marketplace = codexMarketplacePath(options.root)
  // A refusal here still leaves a usable wrapper, so it is a warning, not a throw.
  for (const args of [
    ["plugin", "marketplace", "add", marketplace],
    ["plugin", "add", PLUGIN],
  ]) {
    const result = options.run(target.binary, args, { env })
    if (!result.ok && !/already/i.test(result.stderr + result.stdout)) {
      options.log(`  ! codex ${args.join(" ")} failed: ${(result.stderr || result.stdout).trim().slice(0, 120)}`)
    }
  }

  const wrapper = writeHomeWrapper("codex", {
    binDir: paths.binDir,
    homeEnvVar: "CODEX_HOME",
    homeDir: home,
    guardUrl: options.guardUrl,
    token: options.token,
    profileId: options.profileId,
  })

  if (!hooksTrusted(home)) {
    options.log("  ! hooks are not trusted yet — run `codex-gate`, press `t` on the Hooks screen")
    options.log("    until then a headless run silently skips the gate")
  }
  return { home, wrapper, marketplace, plugin: PLUGIN }
}

export function uninstallCodex(
  target: Detected,
  paths: GatePaths,
  run: Runner,
  log: (line: string) => void,
): void {
  const home = harnessHomeDir(paths, "codex")
  // Unregister while the home still exists, so Codex cleans its own cache.
  if (fs.existsSync(home) && fs.existsSync(target.binary)) {
    const env = { ...process.env, CODEX_HOME: home }
    run(target.binary, ["plugin", "remove", PLUGIN], { env })
    run(target.binary, ["plugin", "marketplace", "remove", MARKETPLACE], { env })
  }
  removeWrapper(paths.binDir, target.id)
  removeHome(home, paths.buildsDir)

  // Earlier versions installed the plugin into the user's own CODEX_HOME and
  // left trust entries behind; strip ours so their config returns to its own.
  const userConfig = path.join(target.configDir, "config.toml")
  if (fs.existsSync(userConfig)) {
    const text = fs.readFileSync(userConfig, "utf8")
    if (text.includes(PLUGIN)) {
      log(`  note: ${userConfig} still mentions ${PLUGIN} — run \`codex plugin remove ${PLUGIN}\` to clear it`)
    }
  }
}
