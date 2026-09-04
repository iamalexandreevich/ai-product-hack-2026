import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { after, describe, it } from "node:test"

import tuiPlugin, { badge, describe as describeMode } from "../src/tui.ts"
import { readMode, writeMode } from "../../core/src/index.ts"

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "gate-tui-"))
const statePath = path.join(tmp, "state.json")
after(() => fs.rmSync(tmp, { recursive: true, force: true }))

type Toast = { title?: string; message: string; variant?: string }

function fakeApi(kind: "keymap" | "legacy" | "none") {
  const toasts: Toast[] = []
  const commands = new Map<string, (arg?: unknown) => void>()
  const api: any = { ui: { toast: (t: Toast) => toasts.push(t) } }

  if (kind === "keymap") {
    api.keymap = {
      registerLayer: (layer: any) => {
        for (const command of layer.commands) commands.set(command.name ?? command.id, command.run)
      },
    }
  } else if (kind === "legacy") {
    api.keymap = {}
    api.command = {
      register: (factory: () => any[]) => {
        for (const command of factory()) commands.set(command.value, command.onSelect)
      },
    }
  }
  return { api, toasts, commands }
}

describe("tui plugin", () => {
  it("binds through the keymap layer when available", async () => {
    writeMode(statePath, "auto")
    const { api, commands, toasts } = fakeApi("keymap")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    assert.ok(commands.has("gate.cycle"), "shift+tab command must be registered")
    assert.ok(toasts.length >= 1, "the current mode should be announced on load")
  })

  it("cycles auto -> ask -> allow -> auto and never reaches off", async () => {
    writeMode(statePath, "auto")
    const { api, commands } = fakeApi("keymap")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    const cycleCommand = commands.get("gate.cycle")!
    cycleCommand()
    assert.equal(readMode(statePath), "ask")
    cycleCommand()
    assert.equal(readMode(statePath), "allow")
    cycleCommand()
    assert.equal(readMode(statePath), "auto")
  })

  it("falls back to the legacy command API when keymap has no registerLayer", async () => {
    writeMode(statePath, "auto")
    const { api, commands } = fakeApi("legacy")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    assert.ok(commands.has("gate.cycle"), "the deprecated path must still bind the command")
    commands.get("gate.cycle")!()
    assert.equal(readMode(statePath), "ask")
  })

  it("loads without crashing when neither binding path exists", async () => {
    const { api, toasts } = fakeApi("none")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    assert.ok(toasts.length >= 1, "the plugin should still report its state")
  })

  it("exposes a palette entry that cycles the mode", async () => {
    // The TUI onSelect receives a dialog, not a text argument, so the palette
    // "set mode" entry cycles like Shift+Tab. Argument-based `/gate <mode>` is
    // the server plugin's job (command.execute.before), tested in plugin.test.ts.
    writeMode(statePath, "auto")
    const { api, commands } = fakeApi("keymap")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    commands.get("gate.set")!()
    assert.equal(readMode(statePath), "ask")
  })

  it("announces the mode with a toast on every change", async () => {
    writeMode(statePath, "auto")
    const { api, commands, toasts } = fakeApi("keymap")
    await tuiPlugin.tui(api, { statePath, logPath: path.join(tmp, "log") })
    const before = toasts.length
    commands.get("gate.cycle")!()
    assert.ok(toasts.length > before, "a mode change must be visible to the user")
    assert.match(toasts[toasts.length - 1].title, /gate: ask/)
  })

  it("labels every mode", () => {
    for (const mode of ["auto", "ask", "allow", "off"] as const) {
      assert.match(badge(mode), new RegExp(mode))
      assert.ok(describeMode(mode).length > 10)
    }
  })
})
