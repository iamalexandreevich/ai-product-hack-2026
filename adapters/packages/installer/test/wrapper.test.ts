import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { renderHomeWrapper, renderWrapper } from "../src/wrapper.ts"

describe("wrappers", () => {
  const base = { binDir: "/bin", guardUrl: "https://guard.example" }

  it("relocates the config dir for harnesses that keep one", () => {
    const script = renderHomeWrapper("codex", {
      ...base,
      homeEnvVar: "CODEX_HOME",
      homeDir: "/home/u/.local/share/gate/home/codex",
    })
    assert.match(script, /export CODEX_HOME='\/home\/u\/\.local\/share\/gate\/home\/codex'/)
    assert.match(script, /exec codex "\$@"/)
  })

  it("omits the home line for harnesses that select by profile", () => {
    const script = renderHomeWrapper("dsh", { ...base, extraArgs: ["--profile", "gate"] })
    // dsh has no home var at all; an empty `export =` line would be a broken script.
    assert.doesNotMatch(script, /^export ='/m)
    assert.match(script, /exec dsh '--profile' 'gate' "\$@"/)
  })

  it("defaults the guard URL instead of forcing it", () => {
    const script = renderHomeWrapper("pi", {
      ...base,
      homeEnvVar: "PI_CODING_AGENT_DIR",
      homeDir: "/h",
      token: "t",
    })
    // An ambient value must win: this is what lets a recording proxy or a
    // staging guard be pointed at without rewriting the wrapper.
    assert.match(script, /AGENTGATE_URL="\$\{AGENTGATE_URL:-https:\/\/guard\.example\}"/)
    assert.match(script, /AGENTGATE_TOKEN="\$\{AGENTGATE_TOKEN:-t\}"/)
  })

  it("quotes a directory containing a quote", () => {
    const script = renderHomeWrapper("pi", {
      ...base,
      homeEnvVar: "PI_CODING_AGENT_DIR",
      homeDir: "/tmp/it's here",
    })
    assert.match(script, /'\/tmp\/it'\\''s here'/)
  })

  it("carries the plugin and its options through the config overlay", () => {
    const target = {
      id: "kilo" as const,
      binary: "/usr/local/bin/kilo",
      version: "7.5.6",
      configDir: "/c",
      configFile: "/c/kilo.jsonc",
      tuiConfigFile: "/c/tui.jsonc",
      envPrefix: "KILO" as const,
    }
    const script = renderWrapper(target, {
      binDir: "/bin",
      pluginSpec: "/bundles/gate.js",
      tuiConfigPath: "/gate/tui/kilo.json",
      targetBinary: "/builds/kilo",
      guardUrl: "https://guard.example",
      token: "t",
    })
    assert.match(script, /export KILO_CONFIG_CONTENT='.*\/bundles\/gate\.js.*'/)
    // The address goes through the environment, and an explicit one still wins:
    // written as a plugin option under the wrong key it was silently dropped and
    // the plugin quietly talked to localhost instead.
    assert.match(script, /export AGENTGATE_URL="\$\{AGENTGATE_URL:-https:\/\/guard\.example\}"/)
    assert.match(script, /export AGENTGATE_TOKEN="\$\{AGENTGATE_TOKEN:-t\}"/)
    assert.match(script, /export KILO_TUI_CONFIG='\/gate\/tui\/kilo\.json'/)
    // Without this the harness updates itself out from under a pinned build.
    assert.match(script, /export KILO_DISABLE_AUTOUPDATE=1/)
    assert.match(script, /exec '\/builds\/kilo' "\$@"/)
  })
})
