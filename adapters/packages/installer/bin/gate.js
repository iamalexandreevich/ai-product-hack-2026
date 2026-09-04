#!/usr/bin/env node
/**
 * `npx @agentgate/gate <command>`.
 *
 * A plain Node CLI with no Bun dependency, so it runs anywhere npm does even
 * though the plugin it installs is loaded by the harness's Bun runtime.
 */
import { install, uninstall, status, doctor, setModeCommand } from "../src/commands.ts"

function parseArgs(argv) {
  const options = {}
  const positional = []
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--yes" || arg === "-y") options.yes = true
    else if (arg === "--guard-url") options.guardUrl = argv[++i]
    else if (arg === "--token") options.token = argv[++i]
    else if (arg === "--profile") options.profileId = argv[++i]
    else if (arg === "--only") options.only = argv[++i].split(",")
    else positional.push(arg)
  }
  return { command: positional[0], rest: positional.slice(1), options }
}

const USAGE = `gate — auto mode for open-source coding agents

  npx @agentgate/gate install [--guard-url URL] [--token T] [--profile P] [--only kilo,opencode,opencode2]
  npx @agentgate/gate status
  npx @agentgate/gate doctor
  npx @agentgate/gate mode <auto|ask|allow|off>
  npx @agentgate/gate uninstall
`

async function main() {
  const { command, rest, options } = parseArgs(process.argv.slice(2))

  switch (command) {
    case "install":
      await install(options)
      break
    case "uninstall":
      uninstall(options)
      break
    case "status": {
      const report = await status(options)
      console.log(`gate mode: ${report.mode}`)
      console.log(`guard: ${report.guardHealthy ? "reachable" : "unreachable"} (${report.guardUrl})`)
      for (const t of report.targets) {
        console.log(`  ${t.id} ${t.version}: ${t.installed ? "installed" : "not installed"} (${t.mode})`)
      }
      break
    }
    case "doctor": {
      const findings = await doctor(options)
      for (const f of findings) {
        const mark = f.level === "ok" ? "✓" : f.level === "warn" ? "!" : "✗"
        console.log(`${mark} ${f.message}`)
      }
      break
    }
    case "mode":
      setModeCommand(rest[0], options)
      break
    default:
      console.log(USAGE)
      process.exit(command ? 1 : 0)
  }
}

main().catch((error) => {
  console.error(`gate: ${error.message}`)
  process.exit(1)
})
