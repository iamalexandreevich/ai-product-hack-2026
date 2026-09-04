/**
 * Gate TUI plugin for opencode 1.x and Kilo CLI.
 *
 * Separate entry point on purpose: the harness types forbid one module from
 * exporting both a server and a TUI plugin (`TuiPluginModule.server?: never`).
 *
 * Binding Shift+Tab is done defensively. `api.keymap.registerLayer` is the
 * current API and exists in the shipped binary, but its signature is not
 * published in the plugin types, so the legacy `api.command.register` — which
 * IS fully typed and still present — is kept as a fallback. Whichever path
 * takes is written to the log, because "the key does nothing" is otherwise
 * indistinguishable from "the plugin did not load".
 */
import {
  createLogger,
  cycle,
  isMode,
  loadConfig,
  readMode,
  writeMode,
} from "../../core/src/index.ts"
import type { GateConfig, Mode } from "../../core/src/index.ts"

const COMMAND_CYCLE = "gate.cycle"
const COMMAND_SET = "gate.set"

export function badge(mode: Mode): string {
  return `⏵ gate: ${mode}`
}

/** Short line explaining what the mode does, for the toast. */
export function describe(mode: Mode): string {
  switch (mode) {
    case "auto":
      return "guard decides; only unclear calls reach you"
    case "ask":
      return "every tool call asks you first"
    case "allow":
      return "no prompts; tool results are still filtered"
    default:
      return "gate is off; the harness behaves as stock"
  }
}

const tui = async (api: any, options: any = {}) => {
  const config: GateConfig = loadConfig(options ?? {})
  const log = createLogger(config.logPath)

  const current = (): Mode => readMode(config.statePath)

  const apply = (next: Mode, via: string): void => {
    writeMode(config.statePath, next)
    log("mode changed", { mode: next, via })
    api?.ui?.toast?.({
      variant: next === "off" ? "warning" : "info",
      title: badge(next),
      message: describe(next),
    })
  }

  const doCycle = (): void => apply(cycle(current()), "shift+tab")

  const doSet = (argument?: unknown): void => {
    const value = String(argument ?? "").trim()
    if (value === "cycle" || !value) return doCycle()
    if (isMode(value)) return apply(value, "slash")
    api?.ui?.toast?.({
      variant: "warning",
      title: badge(current()),
      message: "usage: /gate auto | ask | allow | off",
    })
  }

  // The command registry maps a command's `keybind` to the internal binding
  // shape for us, so "shift+tab" (opencode's own format for this key) binds
  // correctly. `registerLayer` takes a different, unpublished shape and is
  // easy to get silently wrong, so the typed command API is preferred and the
  // raw layer is only a fallback for a host that dropped `command.register`.
  //
  // `/gate <mode>` with an argument is handled by the SERVER plugin's
  // command.execute.before, which is the hook that actually receives the
  // argument string; here we only need the Shift+Tab cycle and a palette entry.
  let bound = false
  const legacyCommands = () => [
    {
      title: `Gate: cycle mode (currently ${current()})`,
      value: COMMAND_CYCLE,
      category: "gate",
      keybind: "shift+tab",
      onSelect: doCycle,
    },
    {
      title: "Gate: set mode (/gate auto|ask|allow|off)",
      value: COMMAND_SET,
      category: "gate",
      slash: { name: "gate" },
      onSelect: () => doSet(),
    },
  ]

  if (typeof api?.command?.register === "function") {
    api.command.register(legacyCommands)
    bound = true
    log("keybinding registered", { via: "command.register" })
  } else if (typeof api?.keymap?.registerLayer === "function") {
    try {
      api.keymap.registerLayer({
        commands: legacyCommands().map((command) => ({
          namespace: "gate",
          name: command.value,
          title: command.title,
          slashName: command.slash?.name,
          run: command.onSelect,
        })),
        bindings: [{ key: "shift+tab", cmd: COMMAND_CYCLE, desc: "Gate: cycle mode" }],
      })
      bound = true
      log("keybinding registered", { via: "keymap.registerLayer" })
    } catch (error) {
      log("keymap.registerLayer failed", { error: String(error) })
    }
  }

  if (!bound) log("no keybinding path available; /gate still works via the server plugin")

  // The badge needs a Solid JSX renderer that external plugins cannot import
  // yet, so the mode is surfaced as a toast on every change instead. Registering
  // the slot is attempted anyway in case the host provides a plain renderer.
  try {
    api?.slots?.register?.({
      session_prompt_right: () => badge(current()),
      home_prompt_right: () => badge(current()),
    })
    log("badge slot registered")
  } catch (error) {
    log("badge slot unavailable, using toasts", { error: String(error) })
  }

  api?.ui?.toast?.({ variant: "info", title: badge(current()), message: "gate active" })
}

export default { id: "gate", tui }
