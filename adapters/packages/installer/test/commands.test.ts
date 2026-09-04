/**
 * Installer tests run entirely against a temp directory tree, so the real
 * machine's config is never touched. A fake `detected` harness with config
 * files inside the temp dir stands in for a real install.
 */
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { after, beforeEach, describe, it } from "node:test"

import { install, uninstall, setModeCommand } from "../src/commands.ts"
import { parseJsonc } from "../src/jsonc.ts"
import { gatePaths } from "../src/paths.ts"

let root = ""
let paths: ReturnType<typeof gatePaths>
let manifestFile = ""

function makeTarget(id: "opencode" | "kilo") {
  const configDir = path.join(root, ".config", id)
  fs.mkdirSync(configDir, { recursive: true })
  const configFile = path.join(configDir, `${id}.jsonc`)
  const tuiConfigFile = path.join(configDir, "tui.jsonc")
  fs.writeFileSync(
    configFile,
    `{
  // user's own config, keep this comment
  "$schema": "https://x/config.json",
  "theme": "dark"
}
`,
  )
  return {
    id,
    binary: `/usr/local/bin/${id}`,
    version: "1.17.18",
    configDir,
    configFile,
    tuiConfigFile,
    envPrefix: id === "kilo" ? ("KILO" as const) : ("OPENCODE" as const),
  }
}

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "gate-installer-"))
  paths = gatePaths(root)
  manifestFile = path.join(root, "manifest.json")
  fs.writeFileSync(manifestFile, "{}") // no patched builds -> fallback
})

after(() => {
  // beforeEach makes a fresh root each test; clean the last one.
  if (root) fs.rmSync(root, { recursive: true, force: true })
})

describe("install (fallback, no build)", () => {
  it("adds the plugin, baseline permission and keybind override", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, guardUrl: "http://g:1", log: () => {} })

    const config = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(Array.isArray(config.plugin), "plugin array should exist")
    assert.equal(config.plugin[0][0], "@agentgate/gate-plugin")
    assert.equal(config.plugin[0][1].guardUrl, "http://g:1")
    assert.equal(config.permission.bash, "ask", "baseline permission needed for ask mode on stock")

    const tui = parseJsonc(fs.readFileSync(target.tuiConfigFile, "utf8"))
    assert.ok(tui.plugin.includes("@agentgate/gate-plugin/tui"))
    assert.equal(tui.keybinds.agent_cycle_reverse, "none")

    assert.equal(parseJsonc(fs.readFileSync(paths.statePath, "utf8")).mode, "auto")
  })

  it("keeps the user's comments and existing keys", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    const text = fs.readFileSync(target.configFile, "utf8")
    assert.match(text, /keep this comment/)
    assert.match(text, /"theme": "dark"/)
    assert.match(text, /"\$schema"/)
  })

  it("takes a .bak before the first edit", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    assert.ok(fs.existsSync(`${target.configFile}.bak`), "a backup must exist")
    assert.doesNotMatch(fs.readFileSync(`${target.configFile}.bak`, "utf8"), /gate-plugin/)
  })

  it("is idempotent: installing twice does not duplicate entries", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    const afterFirst = fs.readFileSync(target.configFile, "utf8")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    const afterSecond = fs.readFileSync(target.configFile, "utf8")
    assert.equal(afterFirst, afterSecond, "second install must be a no-op")
    const config = parseJsonc(afterSecond)
    assert.equal(config.plugin.length, 1, "plugin must appear once")
  })
})

describe("uninstall", () => {
  it("removes our entries and restores the freed keybind", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })

    const config = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(!config.plugin || config.plugin.length === 0, "our plugin must be gone")
    assert.ok(!config.permission, "our baseline permission must be gone")

    const tui = parseJsonc(fs.readFileSync(target.tuiConfigFile, "utf8"))
    assert.ok(!tui.keybinds, "the freed keybind override must be removed")

    // The user's original content survives.
    const text = fs.readFileSync(target.configFile, "utf8")
    assert.match(text, /keep this comment/)
    assert.match(text, /"theme": "dark"/)
  })

  it("leaves a user's own permission rules alone", async () => {
    const target = makeTarget("opencode")
    // User already has a permission block that differs from our baseline.
    fs.writeFileSync(
      target.configFile,
      `{
  "permission": { "bash": "allow", "edit": "ask" }
}
`,
    )
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })
    const config = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(config.permission, "a user's own permission block must not be deleted")
    assert.equal(config.permission.bash, "allow")
  })
})

describe("mode from CLI", () => {
  it("writes the mode when a paths override is given (test mode)", async () => {
    setModeCommand("allow", { paths, log: () => {} })
    assert.equal(parseJsonc(fs.readFileSync(paths.statePath, "utf8")).mode, "allow")
  })

  it("rejects an unknown mode", async () => {
    assert.throws(() => setModeCommand("banana", { paths, log: () => {} }), /unknown mode/)
  })
})

describe("install (pi)", () => {
  it("registers the extension and frees Shift+Tab, reversibly", async () => {
    const configDir = path.join(root, ".pi", "agent")
    fs.mkdirSync(configDir, { recursive: true })
    const target = {
      id: "pi" as const,
      binary: "/usr/local/bin/pi",
      version: "0.84.4",
      configDir,
      configFile: path.join(configDir, "settings.json"),
      tuiConfigFile: path.join(configDir, "keybindings.json"),
      envPrefix: "OPENCODE" as const,
    }
    const prev = process.env.GATE_PI_EXTENSION
    process.env.GATE_PI_EXTENSION = "/abs/path/plugin-pi/src/index.ts"

    await install({ detected: [target], paths, manifestFile, log: () => {} })
    const settings = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(settings.extensions.includes("/abs/path/plugin-pi/src/index.ts"))
    const keys = parseJsonc(fs.readFileSync(target.tuiConfigFile, "utf8"))
    assert.equal(keys["app.thinking.cycle"], "ctrl+shift+t", "Shift+Tab must be freed for the gate cycle")

    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })
    const after = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(!after.extensions || after.extensions.length === 0, "our extension must be removed")
    const keysAfter = fs.existsSync(target.tuiConfigFile)
      ? parseJsonc(fs.readFileSync(target.tuiConfigFile, "utf8"))
      : {}
    assert.ok(!keysAfter["app.thinking.cycle"], "the freed keybind must be restored")

    if (prev === undefined) delete process.env.GATE_PI_EXTENSION
    else process.env.GATE_PI_EXTENSION = prev
  })
})
