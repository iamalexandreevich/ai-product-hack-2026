/**
 * The installer commands. Every file edit is idempotent and comment-preserving,
 * with a .bak taken before the first change, so a second `install` is a no-op
 * and `uninstall` puts things back. The user's original binary and config stay
 * usable throughout: our additions live in the wrapper's overlay (patched) or
 * as clearly-marked plugin entries (fallback).
 */
import fs from "node:fs"
import path from "node:path"
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
import { planInstall, type InstallPlan } from "./plan.ts"
import { ensureBuild } from "./build-fetch.ts"
import { gatePaths, type GatePaths } from "./paths.ts"
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
      return item.startsWith(spec.split("/")[0]) || item.includes("gate-plugin-pi") || item.includes("plugin-pi/src")
    }
    if (Array.isArray(item)) return typeof item[0] === "string" && item[0].startsWith(spec.split("/")[0])
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

  for (const target of targets(options)) {
    const plan = planInstall(target, paths, manifestFile)

    if (plan.mode === "pi") {
      // Register the extension by absolute path so its relative imports of the
      // shared core resolve (Pi resolves relative to the file location).
      const extPath = process.env.GATE_PI_EXTENSION ?? plan.pluginSpec
      ensureInArray(target.configFile, "extensions", extPath)
      // Free Shift+Tab: Pi binds it to app.thinking.cycle by default.
      const addedKeybind = freePiThinkingCycle(target.tuiConfigFile)
      writeRecord(paths, target.id, { addedPermissionKeys: [], addedKeybind })
    } else if (plan.mode === "v2") {
      // opencode 2.0: the plugin must come from an npm registry (plugin add),
      // is cached per exact version, and lives in a background server. So the
      // config carries a version-pinned spec, and the guard URL is passed via
      // the AGENTGATE_URL env the service inherits (the plugin has no options
      // channel through a bare string spec). The installer runs `plugin add`
      // and pins permissions to allow so the harness does not pre-reject before
      // the gate's tool hooks run. A restart picks it up.
      const spec = process.env.GATE_V2_SPEC ?? plan.pluginSpec
      ensureInArray(target.configFile, "plugins", spec)
      // allow-all at the harness layer; gate does the real gating per tool
      setKey(target.configFile, "permissions", [{ action: "*", resource: "*", effect: "allow" }])
      writeRecord(paths, target.id, { addedPermissionKeys: ["__v2_permissions__"], addedKeybind: false })
    } else if (plan.mode === "patched") {
      // Make sure the exact-version build is present and sha256-correct before
      // pointing the wrapper at it. A missing/corrupt build falls back.
      const built = await ensureBuild(manifestFile, target.id, target.version, plan.buildBinary!).catch((error) => {
        log(`  build fetch failed: ${(error as Error).message}`)
        return null
      })
      if (!built) {
        log(`  no usable build for ${target.version}; falling back`)
        ensureInArray(target.configFile, "plugin", [PLUGIN_SPEC_FALLBACK(options), pluginOptions(guardUrl, options)])
        const addedPermissionKeys = fillMissing(target.configFile, "permission", FALLBACK_PERMISSION_BASELINE)
        const addedKeybind = addKeybindOverride(target.tuiConfigFile)
        ensureInArray(target.tuiConfigFile, "plugin", plan.tuiSpec)
        writeRecord(paths, target.id, { addedPermissionKeys, addedKeybind })
        applied.push({ ...plan, mode: "fallback" })
        log(`✓ ${target.id} ${target.version} — fallback (build unavailable)`)
        continue
      }
      writeWrapper(target, {
        binDir: paths.binDir,
        targetBinary: plan.buildBinary!,
        guardUrl,
        token: options.token,
        profileId: options.profileId,
      })
      const addedKeybind = addKeybindOverride(target.tuiConfigFile)
      ensureInArray(target.tuiConfigFile, "plugin", plan.tuiSpec)
      writeRecord(paths, target.id, { addedPermissionKeys: [], addedKeybind })
    } else {
      // Fallback: no build for this version. Load the plugin from config and
      // add baseline ask-rules — but only for tools the user has not already
      // ruled on, so their own permission choices are never overwritten.
      ensureInArray(target.configFile, "plugin", [plan.pluginSpec, pluginOptions(guardUrl, options)])
      const addedPermissionKeys = fillMissing(target.configFile, "permission", FALLBACK_PERMISSION_BASELINE)
      const addedKeybind = addKeybindOverride(target.tuiConfigFile)
      ensureInArray(target.tuiConfigFile, "plugin", plan.tuiSpec)
      writeRecord(paths, target.id, { addedPermissionKeys, addedKeybind })
    }

    applied.push(plan)
    log(`✓ ${target.id} ${target.version} — ${plan.mode}`)
    if (plan.mode === "fallback") {
      log(`  note: no patched build for ${target.version}; the native prompt will flicker on allow`)
    }
    log(`  ${plan.restartHint}`)
  }

  writeMode(paths.statePath, "auto")
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
    if (plan.mode === "pi") {
      removeFromArray(target.configFile, "extensions", isOurPlugin(plan.pluginSpec))
      if (record?.addedKeybind) removeKey(target.tuiConfigFile, "app.thinking.cycle")
    } else {
      removeFromArray(target.configFile, plan.configKey, isOurPlugin(plan.pluginSpec))
      removeFromArray(target.tuiConfigFile, "plugin", isOurPlugin(plan.pluginSpec))
    }
    // Remove exactly the permission keys and keybind we added, nothing else.
    if (record?.addedPermissionKeys?.length) {
      removeObjectKeys(target.configFile, "permission", record.addedPermissionKeys)
    }
    if (record?.addedKeybind) removeKey(target.tuiConfigFile, "keybinds")
    fs.rmSync(recordPath(paths, target.id), { force: true })
    const build = path.join(paths.buildsDir, target.id, target.version)
    if (fs.existsSync(build)) fs.rmSync(build, { recursive: true, force: true })
    log(`✓ removed gate from ${target.id}`)
  }
  log("gate uninstalled; .bak files and state.json were left in place")
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

function PLUGIN_SPEC_FALLBACK(_options: InstallOptions): string {
  return process.env.GATE_PLUGIN_SPEC ?? "@agentgate/gate-plugin"
}

function pluginOptions(guardUrl: string, options: InstallOptions): Record<string, unknown> {
  const opts: Record<string, unknown> = { guardUrl }
  if (options.token) opts.token = options.token
  if (options.profileId) opts.profileId = options.profileId
  return opts
}

export type StatusReport = {
  targets: Array<{ id: string; version: string; mode: string; installed: boolean }>
  mode: string
  guardHealthy: boolean
  guardUrl: string
}

export async function status(options: InstallOptions = {}): Promise<StatusReport> {
  const paths = options.paths ?? gatePaths()
  const guardUrl = options.guardUrl ?? loadConfig().url
  const guard = new GuardClient(loadConfig({ url: guardUrl }))
  const mode = readState(paths.statePath)

  const report: StatusReport = {
    targets: targets(options).map((target) => {
      const plan = planInstall(target, paths, options.manifestFile ?? defaultManifest)
      const wrapper = plan.wrapper ? fs.existsSync(plan.wrapper) : false
      const inConfig = configHasPlugin(target.configFile, plan.pluginSpec)
      return {
        id: target.id,
        version: target.version,
        mode: plan.mode,
        installed: wrapper || inConfig,
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

  const guard = new GuardClient(loadConfig({ url: options.guardUrl }))
  findings.push(
    (await guard.health())
      ? { level: "ok", message: `guard reachable at ${loadConfig({ url: options.guardUrl }).url}` }
      : { level: "warn", message: `guard not reachable at ${loadConfig({ url: options.guardUrl }).url} (fail-open until it returns)` },
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

function configHasPlugin(file: string, spec: string): boolean {
  if (!fs.existsSync(file)) return false
  try {
    const text = fs.readFileSync(file, "utf8").replace(/\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1")
    return text.includes(spec.split("/")[0])
  } catch {
    return false
  }
}
