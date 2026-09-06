/**
 * The installer commands. Every file edit is idempotent and comment-preserving,
 * with a .bak taken before the first change, so a second `install` is a no-op
 * and `uninstall` puts things back. The user's original binary and config stay
 * usable throughout: our additions live in the wrapper's overlay (patched) or
 * as clearly-marked plugin entries (fallback).
 */
import { randomBytes } from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import type { Detected } from "./detect.ts"
import { detectAll, findVsCodeKilo } from "./detect.ts"
import {
  FALLBACK_PERMISSION_BASELINE,
  ensureInArray,
  fillMissing,
  removeFromArray,
  removeKey,
  removeObjectKeys,
  setKey,
} from "./config-edit.ts"
import { parseJsonc } from "./jsonc.ts"
import { planInstall, type InstallPlan } from "./plan.ts"
import { buildBundles, removeBundles } from "./bundle.ts"
import { execRunner, type Runner } from "./harness-cli.ts"
import { startService, waitForService, writeRules } from "./service.ts"
import { hooksTrusted, installCodex, uninstallCodex } from "./install-codex.ts"
import { installDsh, uninstallDsh } from "./install-dsh.ts"
import { installPi, uninstallPi } from "./install-pi.ts"
import { ensureBuild } from "./build-fetch.ts"
import { gatePaths, harnessHomeDir, type GatePaths } from "./paths.ts"
import { removeWrapper, writeWrapper } from "./wrapper.ts"
import { GuardClient } from "../../core/src/client.ts"
import { loadConfig } from "../../core/src/config.ts"
import { writeMode } from "../../core/src/mode.ts"

/**
 * What install added to one target, so uninstall removes exactly that and
 * nothing a user set. Kept in the gate data dir, never in the user's config.
 */
type InstallRecord = {
  addedPermissionKeys: string[]
  addedKeybind: boolean
}

function recordPath(paths: GatePaths, id: string): string {
  return path.join(paths.buildsDir, `installed-${id}.json`)
}

function writeRecord(paths: GatePaths, id: string, record: InstallRecord): void {
  fs.mkdirSync(paths.buildsDir, { recursive: true })
  fs.writeFileSync(recordPath(paths, id), JSON.stringify(record, null, 2))
}

function readRecord(paths: GatePaths, id: string): InstallRecord | null {
  try {
    return JSON.parse(fs.readFileSync(recordPath(paths, id), "utf8")) as InstallRecord
  } catch {
    return null
  }
}

export type InstallOptions = {
  guardUrl?: string
  token?: string
  profileId?: string
  only?: string[]
  paths?: GatePaths
  manifestFile?: string
  /** Injectable for tests so nothing on the real machine is touched. */
  detected?: Detected[]
  /** Same, for the one install step that must call a harness's own CLI. */
  run?: Runner
  /** Checkout the plugins are linked from; overridable so tests stay out of it. */
  repoRoot?: string
  /** Start the bundled guard rather than pointing at one already running. */
  startGuard?: boolean
  /** Stage-2 model for the guard we start. */
  llmUrl?: string
  llmModel?: string
  llmKey?: string
  /** Which ruleset stage 1 gets: low | medium | high. */
  level?: string
  log?: (line: string) => void
}

const defaultManifest = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "../../..",
  "build",
  "manifest.json",
)

function targets(options: InstallOptions): Detected[] {
  const all = options.detected ?? detectAll()
  return options.only?.length ? all.filter((t) => options.only!.includes(t.id)) : all
}

/** Marks a value as ours, so uninstall can find and remove exactly it. */
function isOurPlugin(spec: string): (item: unknown) => boolean {
  return (item) => {
    if (typeof item === "string") {
      return (
        item.startsWith(spec.split("/")[0]) ||
        item.includes("gate-plugin-pi") ||
        item.includes("plugin-pi/src") ||
        // the built bundle, referenced by absolute path
        item.includes("/share/gate/bundles/")
      )
    }
    if (Array.isArray(item)) {
      const head = typeof item[0] === "string" ? item[0] : ""
      return head.startsWith(spec.split("/")[0]) || head.includes("/share/gate/bundles/")
    }
    if (item && typeof item === "object") return String((item as any).package ?? "").includes("gate")
    return false
  }
}

export async function install(options: InstallOptions = {}): Promise<InstallPlan[]> {
  const paths = options.paths ?? gatePaths()
  const log = options.log ?? console.log
  const guardUrl = options.guardUrl ?? "http://127.0.0.1:8400"
  const manifestFile = options.manifestFile ?? defaultManifest
  const applied: InstallPlan[] = []
  const run = options.run ?? execRunner

  // The deterministic ruleset is the user's file: written once, never rewritten.
  writeRules(paths.rulesPath, (options.level ?? "medium") as never, log)

  let effectiveUrl = guardUrl
  if (options.startGuard) {
    const token = options.token ?? randomBytes(16).toString("hex")
    const started = startService({
      token,
      llmUrl: options.llmUrl,
      llmModel: options.llmModel,
      llmKey: options.llmKey,
      run,
      log,
    })
    if (!started.ok) return applied
    effectiveUrl = started.url
    options.token = token
    log("  waiting for the guard to answer…")
    if (await waitForService(effectiveUrl)) log(`✓ guard up at ${effectiveUrl}`)
    else log("  ! the guard did not answer in time — check `docker compose logs` in service/")
  }

  // `@agentgate/gate-plugin` is not on npm, so the harness would silently fail
  // to resolve it. Build a one-file bundle and reference it by path instead.
  const list = targets(options)
  const needsBundle = list.some((t) => t.id === "opencode" || t.id === "kilo")
  const bundles = needsBundle ? buildBundles(paths) : null
  if (needsBundle && !bundles) {
    log("! could not build the plugin bundle (bun missing or sources not found)")
    log("  opencode/kilo would load nothing — install bun, or run from the repo checkout")
  }

  for (const target of list) {
    const plan = planInstall(target, paths, manifestFile)
    const serverSpec = bundles?.server ?? plan.pluginSpec
    const tuiSpec = bundles?.tui ?? plan.tuiSpec

    // Codex keeps TOML and dsh keeps YAML, while every editing helper in this
    // package is JSON-only. Both used to fall through to the fallback branch and
    // have a JSON object written into their config. They are gated by a config
    // directory (or profile) of our own instead, so nothing is written here.
    if (plan.mode === "dsh") {
      const made = installDsh(target, paths, {
        guardUrl: effectiveUrl,
        token: options.token,
        profileId: options.profileId,
        log,
        root: options.repoRoot,
      })
      if (!made) continue
      writeRecord(paths, target.id, {
        addedPermissionKeys: [],
        addedKeybind: false,
        mode: plan.mode,
        created: [made.profile, ...made.links, made.wrapper],
      })
    } else if (plan.mode === "codex") {
      const made = installCodex(target, paths, {
        guardUrl: effectiveUrl,
        token: options.token,
        profileId: options.profileId,
        run: options.run ?? execRunner,
        log,
        root: options.repoRoot,
      })
      if (!made) continue
      writeRecord(paths, target.id, {
        addedPermissionKeys: [],
        addedKeybind: false,
        mode: plan.mode,
        created: [made.home, made.wrapper],
      })
    } else if (plan.mode === "pi") {
      const made = installPi(target, paths, {
        guardUrl: effectiveUrl,
        token: options.token,
        profileId: options.profileId,
        log,
      })
      writeRecord(paths, target.id, {
        addedPermissionKeys: [],
        addedKeybind: false,
        mode: plan.mode,
        created: [made.home, made.wrapper],
      })
    } else if (plan.mode === "v2") {
      // opencode 2.0 loads a plugin only from an npm registry, cached per exact
      // version. Nothing of ours is published, so writing the spec alone would
      // leave a config entry that resolves to nothing while reporting success.
      // Say so instead; the local-registry recipe is in docs/MANUAL-TESTING.md.
      const spec = process.env.GATE_V2_SPEC ?? plan.pluginSpec
      ensureInArray(target.configFile, "plugins", spec)
      // The harness pre-rejects permissions before the gate's tool hooks run, so
      // hand gating to us — but only when the user has no policy of their own.
      const addedTopLevelKeys: string[] = []
      if (!configHasKey(target.configFile, "permissions")) {
        setKey(target.configFile, "permissions", [{ action: "*", resource: "*", effect: "allow" }])
        addedTopLevelKeys.push("permissions")
      }
      writeRecord(paths, target.id, {
        addedPermissionKeys: [],
        addedKeybind: false,
        mode: plan.mode,
        addedTopLevelKeys,
      })
      if (!process.env.GATE_V2_REGISTRY) {
        log("  ! opencode2 resolves plugins only from an npm registry, and gate is not published")
        log("    see docs/MANUAL-TESTING.md \"opencode 2.0\" for the local-registry steps")
      }
    } else if (plan.mode === "patched") {
      // Make sure the exact-version build is present and sha256-correct before
      // pointing the wrapper at it. A missing/corrupt build falls back.
      const built = await ensureBuild(manifestFile, target.id, target.version, plan.buildBinary!).catch((error) => {
        log(`  build fetch failed: ${(error as Error).message}`)
        return null
      })
      if (!built) {
        log(`  no usable build for ${target.version}; falling back`)
        const gateTui = writeGateTuiConfig(paths, target.id, tuiSpec)
        writeWrapper(target, {
          binDir: paths.binDir,
          pluginSpec: serverSpec,
          tuiConfigPath: gateTui,
          // The user's own binary, not a patched one -- that is the whole point:
          // `kilo` keeps behaving as it always did, `kilo-gate` is the gated one.
          targetBinary: target.binary,
          permissionBaseline: FALLBACK_PERMISSION_BASELINE,
          guardUrl: effectiveUrl,
          token: options.token,
          profileId: options.profileId,
        })
        writeRecord(paths, target.id, { addedPermissionKeys: [], addedKeybind: false })
        applied.push({ ...plan, mode: "fallback" })
        log(`✓ ${target.id} ${target.version} — fallback (build unavailable)`)
        log(`  ${plan.restartHint}`)
        continue
      }
      // Our own TUI config, handed to the wrapper. The user's tui.jsonc is left
      // untouched, so the stock binary keeps its own keybinds and shows no badge.
      const gateTui = writeGateTuiConfig(paths, target.id, tuiSpec)
      writeWrapper(target, {
        binDir: paths.binDir,
        pluginSpec: serverSpec,
        tuiConfigPath: gateTui,
        targetBinary: plan.buildBinary!,
        guardUrl: effectiveUrl,
        token: options.token,
        profileId: options.profileId,
      })
      writeRecord(paths, target.id, { addedPermissionKeys: [], addedKeybind: false })
    } else {
      // Fallback: no patched build for this version, so the wrapper execs the
      // stock binary with our plugin and baseline rules overlaid on the
      // environment. Nothing of the user's is edited -- their `kilo` is the
      // same `kilo` it was before, and `kilo-gate` is the gated one.
      const gateTui = writeGateTuiConfig(paths, target.id, tuiSpec)
      writeWrapper(target, {
        binDir: paths.binDir,
        pluginSpec: serverSpec,
        tuiConfigPath: gateTui,
        // The user's own binary, not a patched one -- that is the whole point:
        // `kilo` keeps behaving as it always did, `kilo-gate` is the gated one.
        targetBinary: target.binary,
        permissionBaseline: FALLBACK_PERMISSION_BASELINE,
        guardUrl: effectiveUrl,
        token: options.token,
        profileId: options.profileId,
      })
      writeRecord(paths, target.id, { addedPermissionKeys: [], addedKeybind: false })
    }

    applied.push(plan)
    log(`✓ ${target.id} ${target.version} — ${plan.mode}`)
    if (plan.mode === "fallback") {
      log(`  note: no patched build for ${target.version}; the native prompt will flicker on allow`)
    }
    log(`  ${plan.restartHint}`)
  }

  writeMode(paths.statePath, "auto")
  // Remember which guard was installed, so `status` and `doctor` report on that
  // one rather than on the default nobody chose.
  rememberGuardUrl(paths, effectiveUrl)
  writeGateCli(paths)
  const vscode = findVsCodeKilo()
  if (vscode) log(`  Kilo VS Code found at ${vscode}: use /gate in the panel (Shift+Tab is TUI-only)`)
  log(`gate mode: auto — check with: gate status`)
  return applied
}

export function uninstall(options: InstallOptions = {}): void {
  const paths = options.paths ?? gatePaths()
  const log = options.log ?? console.log

  for (const target of targets(options)) {
    const plan = planInstall(target, paths, options.manifestFile ?? defaultManifest)
    const record = readRecord(paths, target.id)
    removeWrapper(paths.binDir, target.id)
    if (plan.mode === "dsh") {
      uninstallDsh(target, paths, log, options.repoRoot)
    } else if (plan.mode === "codex") {
      uninstallCodex(target, paths, options.run ?? execRunner, log)
    } else if (plan.mode === "pi") {
      uninstallPi(target, paths, log)
    } else {
      removeFromArray(target.configFile, plan.configKey, isOurPlugin(plan.pluginSpec))
      removeFromArray(target.tuiConfigFile, "plugin", isOurPlugin(plan.pluginSpec))
    }
    // Remove exactly the permission keys and keybind we added, nothing else.
    for (const key of record?.addedTopLevelKeys ?? []) removeKey(target.configFile, key)
    if (plan.mode !== "codex" && plan.mode !== "dsh" && record?.addedPermissionKeys?.length) {
      removeObjectKeys(target.configFile, "permission", record.addedPermissionKeys)
    }
    if (plan.mode !== "codex" && plan.mode !== "dsh" && record?.addedKeybind) {
      removeKey(target.tuiConfigFile, "keybinds")
    }
    fs.rmSync(recordPath(paths, target.id), { force: true })
    const build = path.join(paths.buildsDir, target.id, target.version)
    if (fs.existsSync(build)) fs.rmSync(build, { recursive: true, force: true })
    log(`✓ removed gate from ${target.id}`)
  }
  log("gate uninstalled; .bak files and state.json were left in place")
  removeBundles(paths)
  fs.rmSync(path.join(paths.binDir, "gate"), { force: true })
}



/** Frees Shift+Tab, reporting whether we were the one who set it (so uninstall
 *  only reverts our own override, not a user's). */
function addKeybindOverride(tuiConfigFile: string): boolean {
  let existing: any = {}
  try {
    if (fs.existsSync(tuiConfigFile)) {
      const text = fs.readFileSync(tuiConfigFile, "utf8").replace(/\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1")
      existing = JSON.parse(text || "{}")
    }
  } catch {
    existing = {}
  }
  if (existing?.keybinds?.agent_cycle_reverse !== undefined) return false
  setKey(tuiConfigFile, "keybinds", { ...(existing.keybinds ?? {}), agent_cycle_reverse: "none" })
  return true
}

/** Rebinds Pi's app.thinking.cycle off Shift+Tab so the gate cycle can claim
 *  it. Reports whether we changed anything (for a clean uninstall). */
function freePiThinkingCycle(keybindingsFile: string): boolean {
  let existing: any = {}
  try {
    if (fs.existsSync(keybindingsFile)) {
      const text = fs.readFileSync(keybindingsFile, "utf8").replace(/\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1")
      existing = JSON.parse(text || "{}")
    }
  } catch {
    existing = {}
  }
  // Only touch it if the user has not set their own binding for it.
  if (existing["app.thinking.cycle"] !== undefined) return false
  setKey(keybindingsFile, "app.thinking.cycle", "ctrl+shift+t")
  return true
}


/** `url`, not `guardUrl`: that is the key `loadConfig` reads. Writing the other
 *  name dropped the address without a word and sent the plugin to localhost. */
function pluginOptions(guardUrl: string, options: InstallOptions): Record<string, unknown> {
  const opts: Record<string, unknown> = { url: guardUrl }
  if (options.token) opts.token = options.token
  if (options.profileId) opts.profileId = options.profileId
  return opts
}

export type StatusReport = {
  targets: Array<{ id: string; version: string; mode: string; installed: boolean; wrapper: string | null }>
  mode: string
  guardHealthy: boolean
  guardUrl: string
}

export async function status(options: InstallOptions = {}): Promise<StatusReport> {
  const paths = options.paths ?? gatePaths()
  const guardUrl = options.guardUrl ?? installedGuardUrl(paths) ?? loadConfig().url
  const guard = new GuardClient(loadConfig({ url: guardUrl }))
  const mode = readState(paths.statePath)

  const report: StatusReport = {
    targets: targets(options).map((target) => {
      const plan = planInstall(target, paths, options.manifestFile ?? defaultManifest)
      const wrapper = plan.wrapper ? fs.existsSync(plan.wrapper) : false
      return {
        id: target.id,
        version: target.version,
        mode: plan.mode,
        installed: isInstalled(plan, paths, target),
        wrapper: plan.wrapper && wrapper ? plan.wrapper : null,
      }
    }),
    mode,
    guardHealthy: await guard.health(),
    guardUrl,
  }
  return report
}

export type DoctorFinding = { level: "ok" | "warn" | "error"; message: string }

export async function doctor(options: InstallOptions = {}): Promise<DoctorFinding[]> {
  const paths = options.paths ?? gatePaths()
  const findings: DoctorFinding[] = []
  const found = targets(options)

  if (!found.length) findings.push({ level: "warn", message: "no supported harness found on PATH" })

  for (const target of found) {
    const plan = planInstall(target, paths, options.manifestFile ?? defaultManifest)
    if (plan.mode === "codex") {
      const home = harnessHomeDir(paths, "codex")
      if (!plan.wrapper || !fs.existsSync(plan.wrapper)) {
        findings.push({ level: "error", message: "codex found but codex-gate is missing — run install" })
      } else if (!hooksTrusted(home)) {
        // The failure that looks like success: an untrusted hook is skipped in
        // headless runs without a word, so the gate is simply not there.
        findings.push({
          level: "warn",
          message: "codex hooks are not trusted — run `codex-gate`, press `t` on the Hooks screen",
        })
      }
    }
    if (plan.mode === "dsh") {
      const patch = path.join(target.configDir, "gate", "cordis.patch.yml")
      if (!fs.existsSync(patch)) {
        findings.push({ level: "error", message: "dsh gate profile missing — run install" })
      }
    }
    if (plan.mode === "pi") {
      const settings = path.join(harnessHomeDir(paths, "pi"), "settings.json")
      if (fs.existsSync(target.configFile) && fs.readFileSync(target.configFile, "utf8").includes("plugin-pi")) {
        findings.push({
          level: "warn",
          message: "your own Pi config still registers the gate extension — run uninstall to clear it",
        })
      }
      if (!fs.existsSync(settings)) {
        findings.push({ level: "error", message: "pi gate config dir missing — run install" })
      }
    }
    if (plan.mode === "fallback") {
      findings.push({
        level: "warn",
        message: `${target.id} ${target.version}: no patched build; running in fallback (prompt flickers on allow, ask needs baseline rules)`,
      })
    }
    if (plan.mode === "patched" && plan.buildBinary && !fs.existsSync(plan.buildBinary)) {
      findings.push({
        level: "error",
        message: `${target.id}: manifest lists a build for ${target.version} but ${plan.buildBinary} is missing`,
      })
    }
    if (process.env.KILO_PURE === "1" || process.argv.includes("--pure")) {
      findings.push({ level: "warn", message: "KILO_PURE/--pure disables external plugins; gate will not load" })
    }
  }

  // Wrappers that exist and cannot be found are the commonest "installed but
  // nothing happens" — worth checking before anything subtler.
  if (!(process.env.PATH ?? "").split(":").includes(paths.binDir)) {
    findings.push({
      level: "warn",
      message: `${paths.binDir} is not on your PATH — the -gate commands exist but will not be found`,
    })
  }

  const doctorUrl = options.guardUrl ?? installedGuardUrl(paths) ?? loadConfig().url
  const guard = new GuardClient(loadConfig({ url: doctorUrl }))
  findings.push(
    (await guard.health())
      ? { level: "ok", message: `guard reachable at ${doctorUrl}` }
      : { level: "warn", message: `guard not reachable at ${doctorUrl} (fail-open until it returns)` },
  )
  return findings
}

export function setModeCommand(mode: string, options: InstallOptions = {}): void {
  const paths = options.paths ?? gatePaths()
  const log = options.log ?? console.log
  if (!["auto", "ask", "allow", "off"].includes(mode)) {
    throw new Error(`unknown mode "${mode}"; use auto | ask | allow | off`)
  }
  // From the CLI, only interactively — an agent must not disable its own guard
  // by shelling out. `/gate` from the chat is a human keystroke and stays open.
  if (!process.stdout.isTTY && !options.paths) {
    throw new Error("refusing to change mode from a non-interactive shell; use /gate in the TUI")
  }
  writeMode(paths.statePath, mode as any)
  log(`gate mode: ${mode}`)
}

function readState(statePath: string): string {
  try {
    return JSON.parse(fs.readFileSync(statePath, "utf8")).mode ?? "auto"
  } catch {
    return "auto"
  }
}

/**
 * Our entry is written either as an npm spec or as a path to a built bundle /
 * extension source, so looking for the package name alone reports "not
 * installed" on a perfectly good install.
 */
/**
 * Whether gate is installed for this target. For the harnesses gated by a
 * config directory of our own, the user's config is correctly empty — looking
 * there would always report "not installed". Ask for proof the thing we made
 * exists instead.
 */
function isInstalled(plan: InstallPlan, paths: GatePaths, target: Detected): boolean {
  const wrapper = plan.wrapper ? fs.existsSync(plan.wrapper) : false
  if (plan.mode === "codex") {
    // The wrapper alone proves nothing: the plugin must really be in our home.
    return wrapper && fs.existsSync(path.join(harnessHomeDir(paths, "codex"), "plugins"))
  }
  if (plan.mode === "pi") {
    const settings = path.join(harnessHomeDir(paths, "pi"), "settings.json")
    return wrapper && fs.existsSync(settings) && fs.readFileSync(settings, "utf8").includes("plugin-pi")
  }
  if (plan.mode === "dsh") {
    const patch = path.join(target.configDir, "gate", "cordis.patch.yml")
    return wrapper && fs.existsSync(patch) && fs.readFileSync(patch, "utf8").includes("@agentgate/dsh-gate")
  }
  return wrapper || configHasPlugin(target.configFile, plan.pluginSpec)
}

/** Whether the config already declares a top-level key, so we never clobber it. */
function configHasKey(file: string, key: string): boolean {
  if (!fs.existsSync(file)) return false
  try {
    return Object.prototype.hasOwnProperty.call(parseJsonc(fs.readFileSync(file, "utf8")) as object, key)
  } catch {
    return false
  }
}

function configHasPlugin(file: string, spec: string): boolean {
  if (!fs.existsSync(file)) return false
  try {
    const text = fs.readFileSync(file, "utf8").replace(/\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1")
    return (
      text.includes(spec.split("/")[0]) ||
      text.includes("/share/gate/bundles/") ||
      text.includes("plugin-pi/src")
    )
  } catch {
    return false
  }
}

/**
 * A `gate` command on PATH. Without it the only way to switch modes outside a
 * TUI is spelling out the installer's entry point, which nobody remembers.
 */
function writeGateCli(paths: GatePaths): void {
  const entry = path.resolve(fileURLToPath(import.meta.url), "..", "..", "bin", "gate.js")
  if (!fs.existsSync(entry)) return
  fs.mkdirSync(paths.binDir, { recursive: true })
  const file = path.join(paths.binDir, "gate")
  fs.writeFileSync(
    file,
    ["#!/bin/sh", "# Generated by @agentgate/gate.", `exec node '${entry}' "$@"`, ""].join("\n"),
  )
  fs.chmodSync(file, 0o755)
}

/**
 * The TUI half of the plugin, in a config only the wrapper reads. Keeping it out
 * of the harness's own tui config is what makes `kilo` and `kilo-gate` genuinely
 * different: the badge and Shift+Tab belong to the gated build alone.
 */
function writeGateTuiConfig(paths: GatePaths, id: string, tuiSpec: string): string {
  const dir = path.join(paths.buildsDir, "tui")
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, `${id}.json`)
  fs.writeFileSync(
    file,
    JSON.stringify({ keybinds: { agent_cycle_reverse: "none" }, plugin: [tuiSpec] }, null, 2) + "\n",
  )
  return file
}

/** The installed guard's address, kept beside the mode in gate's own state. */
function rememberGuardUrl(paths: GatePaths, url: string): void {
  try {
    const file = path.join(path.dirname(paths.statePath), "guard.json")
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, JSON.stringify({ url }, null, 2) + "\n")
  } catch {
    // Losing this only costs a less accurate status line.
  }
}

export function installedGuardUrl(paths: GatePaths): string | null {
  try {
    const file = path.join(path.dirname(paths.statePath), "guard.json")
    return (JSON.parse(fs.readFileSync(file, "utf8")) as { url?: string }).url ?? null
  } catch {
    return null
  }
}
