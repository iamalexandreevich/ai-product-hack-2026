/**
 * Installer tests run entirely against a temp directory tree, so the real
 * machine's config is never touched. A fake `detected` harness with config
 * files inside the temp dir stands in for a real install.
 */
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { after, beforeEach, describe, it } from "node:test"

import { install, uninstall, setModeCommand } from "../src/commands.ts"
import { currentPlatform } from "../src/manifest.ts"
import { parseJsonc } from "../src/jsonc.ts"
import { gatePaths } from "../src/paths.ts"

let root = ""
let paths: ReturnType<typeof gatePaths>
let manifestFile = ""

function makeTarget(id: "opencode" | "kilo" | "codex" | "dsh" | "pi") {
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
    // The spec must be something the harness can actually resolve. An npm name
    // that is not published loads nothing and fails silently, which is exactly
    // how this went wrong once: the config looked right and the plugin never ran.
    const spec = config.plugin[0][0] as string
    assert.ok(path.isAbsolute(spec), `plugin spec should be a path, got ${spec}`)
    assert.ok(fs.existsSync(spec), `plugin bundle should exist at ${spec}`)
    // `url` is the key the plugin config actually reads.
    assert.equal(config.plugin[0][1].url, "http://g:1")
    assert.equal(config.permission.bash, "ask", "baseline permission needed for ask mode on stock")

    const tui = parseJsonc(fs.readFileSync(target.tuiConfigFile, "utf8"))
    assert.ok(tui.plugin.some((p: string) => fs.existsSync(p)), "tui plugin bundle should exist")
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
  function makePiTarget() {
    const configDir = path.join(root, ".pi", "agent")
    fs.mkdirSync(configDir, { recursive: true })
    fs.writeFileSync(path.join(configDir, "settings.json"), '{\n  "theme": "dark"\n}\n')
    return {
      id: "pi" as const,
      binary: "/usr/local/bin/pi",
      version: "0.84.4",
      configDir,
      configFile: path.join(configDir, "settings.json"),
      tuiConfigFile: path.join(configDir, "keybindings.json"),
      envPrefix: "OPENCODE" as const,
      homeEnvVar: "PI_CODING_AGENT_DIR" as const,
    }
  }

  const EXT = "/abs/path/plugin-pi/src/index.ts"

  it("registers the extension in gate's own home, not the user's", async () => {
    const target = makePiTarget()
    const before = fs.readFileSync(target.configFile, "utf8")
    const prev = process.env.GATE_PI_EXTENSION
    process.env.GATE_PI_EXTENSION = EXT
    try {
      await install({ detected: [target], paths, manifestFile, log: () => {} })

      // The user's own pi must keep running ungated.
      assert.equal(fs.readFileSync(target.configFile, "utf8"), before)

      const home = path.join(paths.buildsDir, "home", "pi")
      const settings = parseJsonc(fs.readFileSync(path.join(home, "settings.json"), "utf8"))
      assert.ok(settings.extensions.includes(EXT), "extension goes into our copy")
      assert.equal(settings.theme, "dark", "the copy keeps the user's own settings")

      const keys = parseJsonc(fs.readFileSync(path.join(home, "keybindings.json"), "utf8"))
      assert.equal(keys["app.thinking.cycle"], "ctrl+shift+t", "Shift+Tab freed for the gate cycle")
    } finally {
      if (prev === undefined) delete process.env.GATE_PI_EXTENSION
      else process.env.GATE_PI_EXTENSION = prev
    }
  })

  it("writes a pi-gate wrapper that relocates the config dir", async () => {
    const target = makePiTarget()
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    const wrapper = fs.readFileSync(path.join(paths.binDir, "pi-gate"), "utf8")
    assert.match(wrapper, /export PI_CODING_AGENT_DIR=/)
    // An ambient value must win, so recording and staging setups can redirect it.
    assert.match(wrapper, /AGENTGATE_URL="\$\{AGENTGATE_URL:-/)
    assert.match(wrapper, /exec pi "\$@"/)
  })

  it("uninstall removes our home and wrapper and leaves the user's config alone", async () => {
    const target = makePiTarget()
    const before = fs.readFileSync(target.configFile, "utf8")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })

    assert.ok(!fs.existsSync(path.join(paths.buildsDir, "home", "pi")), "home removed")
    assert.ok(!fs.existsSync(path.join(paths.binDir, "pi-gate")), "wrapper removed")
    assert.equal(fs.readFileSync(target.configFile, "utf8"), before)
  })

  it("cleans a legacy install that edited the user's own config", async () => {
    const target = makePiTarget()
    // What older versions of the installer left behind.
    fs.writeFileSync(target.configFile, JSON.stringify({ extensions: [EXT], theme: "dark" }, null, 2))
    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })
    const after = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    assert.ok(!after.extensions?.length, "the stranded registration is removed")
    assert.equal(after.theme, "dark", "the user's own keys survive")
  })
})

describe("install (patched build)", () => {
  /** A manifest with a real file so planInstall picks the patched path. */
  function withPatchedBuild(target: ReturnType<typeof makeTarget>) {
    const build = path.join(root, "catalog", target.id)
    fs.mkdirSync(path.dirname(build), { recursive: true })
    fs.writeFileSync(build, "#!/bin/sh\nexit 0\n")
    const sha = createHash("sha256").update(fs.readFileSync(build)).digest("hex")
    fs.writeFileSync(
      manifestFile,
      JSON.stringify({
        [target.id]: { [target.version]: { [currentPlatform()]: { url: `file://${build}`, sha256: sha } } },
      }),
    )
  }

  it("keeps the stock binary clean: no badge without gating", async () => {
    const target = makeTarget("kilo")
    withPatchedBuild(target)
    await install({ detected: [target], paths, manifestFile, log: () => {} })

    // The TUI plugin draws a "gate: auto" badge. If it lived in the harness's
    // own tui config, plain `kilo` would advertise protection while the server
    // plugin — the part that actually gates — is injected by the wrapper only.
    const shared = fs.existsSync(target.tuiConfigFile)
      ? fs.readFileSync(target.tuiConfigFile, "utf8")
      : ""
    assert.ok(!shared.includes("gate-tui"), "TUI plugin must not reach the shared tui config")

    const wrapper = fs.readFileSync(path.join(paths.binDir, "kilo-gate"), "utf8")
    assert.match(wrapper, /KILO_TUI_CONFIG=/, "wrapper should point at gate's own tui config")
    const tuiConfig = wrapper.match(/KILO_TUI_CONFIG='([^']+)'/)?.[1]
    assert.ok(tuiConfig && fs.existsSync(tuiConfig), "gate tui config should exist")
    assert.match(fs.readFileSync(tuiConfig!, "utf8"), /gate-tui/)
  })
})

describe("codex", () => {
  const TOML = 'model = "gpt-5.6-sol"\n[hooks.state]\n'

  function makeCodexTarget() {
    const configDir = path.join(root, ".codex")
    fs.mkdirSync(path.join(configDir, "sessions"), { recursive: true })
    fs.writeFileSync(path.join(configDir, "config.toml"), TOML)
    fs.writeFileSync(path.join(configDir, "auth.json"), '{"token":"user"}')
    fs.writeFileSync(path.join(configDir, "sessions", "keepme"), "important")
    return {
      id: "codex" as const,
      binary: "/usr/local/bin/codex",
      version: "0.146.0",
      configDir,
      configFile: path.join(configDir, "config.toml"),
      tuiConfigFile: path.join(configDir, "config.toml"),
      envPrefix: "OPENCODE" as const,
      homeEnvVar: "CODEX_HOME" as const,
    }
  }

  /** A stand-in checkout so vendoring never rewrites the real marketplace. */
  function fakeRepo() {
    const repo = path.join(root, "repo")
    const core = path.join(repo, "packages", "core", "src")
    fs.mkdirSync(core, { recursive: true })
    fs.writeFileSync(path.join(core, "index.ts"), "export const x = 1\n")
    fs.mkdirSync(path.join(repo, "packages", "plugin-codex", "marketplace"), { recursive: true })
    return repo
  }

  function recordingRunner() {
    const calls: Array<{ cmd: string; args: string[]; home?: string }> = []
    const run = (cmd: string, args: string[], opts?: { env?: NodeJS.ProcessEnv }) => {
      calls.push({ cmd, args, home: opts?.env?.CODEX_HOME })
      return { ok: true, code: 0, stdout: "", stderr: "" }
    }
    return { calls, run }
  }

  it("writes nothing into the user's config.toml", async () => {
    const target = makeCodexTarget()
    const { run } = recordingRunner()
    await install({ detected: [target], paths, manifestFile, run, repoRoot: fakeRepo(), log: () => {} })

    // The JSONC helpers would have written a JSON object into TOML. Byte
    // equality plus the absence of a .bak proves none of them ran on it.
    assert.equal(fs.readFileSync(target.configFile, "utf8"), TOML)
    assert.ok(!fs.existsSync(`${target.configFile}.bak`), "no backup means no editing helper touched it")
  })

  it("prepares a home: config copied, shared state linked", async () => {
    const target = makeCodexTarget()
    const { run } = recordingRunner()
    await install({ detected: [target], paths, manifestFile, run, repoRoot: fakeRepo(), log: () => {} })

    const home = path.join(paths.buildsDir, "home", "codex")
    assert.equal(fs.readFileSync(path.join(home, "config.toml"), "utf8"), TOML)
    // config.toml is a copy because `plugin add` writes into it; credentials are
    // linked so a login in one is a login in both.
    assert.ok(!fs.lstatSync(path.join(home, "config.toml")).isSymbolicLink())
    assert.ok(fs.lstatSync(path.join(home, "auth.json")).isSymbolicLink())
    assert.ok(!fs.existsSync(path.join(home, "models_cache.json")), "absent names are skipped, not dangling")
  })

  it("registers the plugin under our CODEX_HOME, not the user's", async () => {
    const target = makeCodexTarget()
    const { calls, run } = recordingRunner()
    await install({ detected: [target], paths, manifestFile, run, repoRoot: fakeRepo(), log: () => {} })

    const home = path.join(paths.buildsDir, "home", "codex")
    assert.equal(calls.length, 2)
    assert.deepEqual(calls[0].args.slice(0, 3), ["plugin", "marketplace", "add"])
    assert.ok(fs.existsSync(calls[0].args[3]), "marketplace path must exist")
    assert.deepEqual(calls[1].args, ["plugin", "add", "gate@agentgate"])
    for (const call of calls) assert.equal(call.home, home, "every write must land in our home")
  })

  it("uninstall does not follow symlinks out of our home", async () => {
    const target = makeCodexTarget()
    const { run } = recordingRunner()
    await install({ detected: [target], paths, manifestFile, run, repoRoot: fakeRepo(), log: () => {} })
    await uninstall({ detected: [target], paths, manifestFile, run, repoRoot: fakeRepo(), log: () => {} })

    assert.ok(!fs.existsSync(path.join(paths.buildsDir, "home", "codex")), "home removed")
    // The link pointed at the user's real sessions; removing the home must not
    // have descended through it.
    assert.equal(fs.readFileSync(path.join(target.configDir, "sessions", "keepme"), "utf8"), "important")
    assert.equal(fs.readFileSync(target.configFile, "utf8"), TOML)
  })
})

describe("dsh", () => {
  const YAML_DONOR = "plugins:\n  - id: llm\n"

  /** A stand-in checkout, so the tests never link into the real working tree. */
  function fakeRepo() {
    const repo = path.join(root, "repo")
    fs.mkdirSync(path.join(repo, "packages", "plugin-dsh"), { recursive: true })
    fs.mkdirSync(path.join(repo, "packages", "core"), { recursive: true })
    return repo
  }

  function makeDshTarget() {
    const profilesDir = path.join(root, ".dsh", "profiles")
    const donor = path.join(profilesDir, "headless")
    fs.mkdirSync(donor, { recursive: true })
    fs.writeFileSync(path.join(donor, "cordis.yml"), YAML_DONOR)
    fs.writeFileSync(path.join(donor, "package.json"), '{"name":"dsh-profile-headless"}')
    fs.writeFileSync(path.join(donor, "pnpm-workspace.yaml"), "packages: []\n")
    return {
      id: "dsh" as const,
      binary: "/usr/local/bin/dsh",
      version: "0.1.1-rc.2",
      configDir: profilesDir,
      configFile: path.join(profilesDir, "gate", "cordis.patch.yml"),
      tuiConfigFile: path.join(profilesDir, "gate", "cordis.patch.yml"),
      envPrefix: "OPENCODE" as const,
    }
  }

  it("writes YAML, not JSON, and leaves the donor alone", async () => {
    const target = makeDshTarget()
    const donorBefore = fs.readFileSync(path.join(target.configDir, "headless", "cordis.yml"), "utf8")
    await install({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: () => {} })

    const patch = fs.readFileSync(target.configFile, "utf8")
    assert.match(patch, /^- insert:/m, "the profile patch is YAML")
    assert.doesNotMatch(patch, /^\{/, "a JSON object here would compose to nothing")
    // `insert` is required: a bare `- id: gate` is a patch of an existing entry.
    assert.match(patch, /insert:[\s\S]*id: gate/)
    assert.equal(fs.readFileSync(path.join(target.configDir, "headless", "cordis.yml"), "utf8"), donorBefore)
  })

  it("renames the copied profile so two packages do not share one name", async () => {
    const target = makeDshTarget()
    await install({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: () => {} })
    const pkg = JSON.parse(fs.readFileSync(path.join(target.configDir, "gate", "package.json"), "utf8"))
    assert.equal(pkg.name, "dsh-profile-gate")
  })

  it("wrapper boots our profile", async () => {
    const target = makeDshTarget()
    await install({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: () => {} })
    const wrapper = fs.readFileSync(path.join(paths.binDir, "dsh-gate"), "utf8")
    assert.match(wrapper, /exec dsh '--profile' 'gate' "\$@"/)
    assert.doesNotMatch(wrapper, /^export =/m, "no home var for dsh")
  })

  it("says what to do when there is no profile to copy", async () => {
    const target = makeDshTarget()
    fs.rmSync(path.join(target.configDir, "headless"), { recursive: true, force: true })
    const lines: string[] = []
    await install({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: (l) => lines.push(l) })
    assert.match(lines.join("\n"), /dsh --profile headless --help/)
    assert.ok(!fs.existsSync(path.join(target.configDir, "gate")), "nothing half-made is left behind")
  })

  it("uninstall removes our profile and leaves the donor", async () => {
    const target = makeDshTarget()
    await install({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: () => {} })
    await uninstall({ detected: [target], paths, manifestFile, repoRoot: fakeRepo(), log: () => {} })
    assert.ok(!fs.existsSync(path.join(target.configDir, "gate")))
    assert.ok(fs.existsSync(path.join(target.configDir, "headless", "cordis.yml")))
  })
})

describe("rules and the guard service", () => {
  it("writes the chosen level and applies to every harness at once", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, level: "high", log: () => {} })

    const rules = JSON.parse(fs.readFileSync(paths.rulesPath, "utf8"))
    assert.equal(rules.level, "high")
    assert.equal(rules.version, 1)
    // One file, not one per harness: policy that differs by agent is policy
    // nobody can reason about.
    assert.ok(rules.deny.includes("sudo *"))
    assert.ok(rules.allow.includes("git status"))
    assert.ok(Array.isArray(rules.ask))
  })

  it("never overwrites a ruleset the user has edited", async () => {
    const target = makeTarget("opencode")
    fs.mkdirSync(path.dirname(paths.rulesPath), { recursive: true })
    const mine = { version: 1, level: "custom", allow: ["my-tool *"], ask: [], deny: ["rm *"] }
    fs.writeFileSync(paths.rulesPath, JSON.stringify(mine, null, 2))

    await install({ detected: [target], paths, manifestFile, level: "low", log: () => {} })
    assert.deepEqual(JSON.parse(fs.readFileSync(paths.rulesPath, "utf8")), mine)
  })

  it("defaults to medium when no level is given", async () => {
    const target = makeTarget("opencode")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    assert.equal(JSON.parse(fs.readFileSync(paths.rulesPath, "utf8")).level, "medium")
  })

  it("starts the bundled guard and generates a token when asked", async () => {
    const target = makeTarget("opencode")
    const calls: Array<{ cmd: string; args: string[]; env?: NodeJS.ProcessEnv }> = []
    const run = (cmd: string, args: string[], opts?: { env?: NodeJS.ProcessEnv }) => {
      calls.push({ cmd, args, env: opts?.env })
      return { ok: true, code: 0, stdout: "", stderr: "" }
    }
    const dir = path.join(root, "service")
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, "docker-compose.yml"), "services: {}\n")

    const { startService } = await import("../src/service.ts")
    const started = startService({
      dir,
      token: "generated",
      llmUrl: "https://llm.example/v1",
      llmModel: "m",
      llmKey: "k",
      run,
      log: () => {},
    })

    assert.ok(started.ok)
    assert.deepEqual(calls[0].args, ["compose", "up", "-d", "--build"])
    // The compose file refuses to start without a token, so it must be passed.
    assert.equal(calls[0].env?.AGENTGATE_TOKEN, "generated")
    assert.equal(calls[0].env?.OPENROUTER_MODEL_NAME, "m")
  })

  it("says so when there is no LLM key: stage 2 fails closed to ask", async () => {
    const lines: string[] = []
    const dir = path.join(root, "service")
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, "docker-compose.yml"), "services: {}\n")
    const { startService } = await import("../src/service.ts")
    startService({
      dir,
      token: "t",
      run: () => ({ ok: true, code: 0, stdout: "", stderr: "" }),
      log: (l) => lines.push(l),
    })
    assert.match(lines.join("\n"), /stage 2 will fail closed/)
  })
})

describe("opencode2", () => {
  function makeV2Target() {
    const configDir = path.join(root, ".config", "opencode")
    fs.mkdirSync(configDir, { recursive: true })
    const configFile = path.join(configDir, "opencode.jsonc")
    return {
      id: "opencode2" as const,
      binary: "/usr/local/bin/opencode2",
      version: "0.0.0-beta-17823",
      configDir,
      configFile,
      tuiConfigFile: path.join(configDir, "tui.jsonc"),
      envPrefix: "OPENCODE" as const,
    }
  }

  it("removes the permissions block it added", async () => {
    const target = makeV2Target()
    fs.writeFileSync(target.configFile, "{}\n")
    await install({ detected: [target], paths, manifestFile, log: () => {} })
    assert.ok(parseJsonc(fs.readFileSync(target.configFile, "utf8")).permissions, "added on install")

    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })
    const after = parseJsonc(fs.readFileSync(target.configFile, "utf8"))
    // It used to be written as `permissions` and removed as `permission`, so the
    // block outlived uninstall and kept the harness wide open.
    assert.ok(!after.permissions, "must not survive uninstall")
  })

  it("never overwrites a permissions policy the user wrote", async () => {
    const target = makeV2Target()
    const mine = [{ action: "bash", resource: "*", effect: "deny" }]
    fs.writeFileSync(target.configFile, JSON.stringify({ permissions: mine }, null, 2))

    await install({ detected: [target], paths, manifestFile, log: () => {} })
    assert.deepEqual(parseJsonc(fs.readFileSync(target.configFile, "utf8")).permissions, mine)

    await uninstall({ detected: [target], paths, manifestFile, log: () => {} })
    assert.deepEqual(parseJsonc(fs.readFileSync(target.configFile, "utf8")).permissions, mine)
  })

  it("says plainly that the plugin cannot resolve without a registry", async () => {
    const target = makeV2Target()
    fs.writeFileSync(target.configFile, "{}\n")
    const lines: string[] = []
    await install({ detected: [target], paths, manifestFile, log: (l) => lines.push(l) })
    // Writing a spec that resolves to nothing and reporting success is worse
    // than saying what is missing.
    assert.match(lines.join("\n"), /npm registry/)
  })
})
