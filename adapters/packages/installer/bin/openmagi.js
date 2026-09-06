#!/usr/bin/env node
/**
 * The OPENMAGI CLI.
 *
 * A plain Node CLI with no Bun dependency, so it runs anywhere node does even
 * though the plugin it installs is loaded by the harness's Bun runtime. Run it
 * from a checkout; nothing is published to npm, so `npx @agentgate/gate` would
 * only look like it works.
 */
import { install, uninstall, status, doctor, setModeCommand } from "../src/commands.ts"
import { runWizard } from "../src/wizard.ts"
import { mark, wordmark } from "../src/brand.ts"

function parseArgs(argv) {
  const options = {}
  const positional = []
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--yes" || arg === "-y") options.yes = true
    else if (arg === "--guard-url") options.guardUrl = argv[++i]
    else if (arg === "--token") options.token = argv[++i]
    else if (arg === "--profile") options.profileId = argv[++i]
    else if (arg === "--start-guard") options.startGuard = true
    else if (arg === "--llm-url") options.llmUrl = argv[++i]
    else if (arg === "--llm-model") options.llmModel = argv[++i]
    else if (arg === "--llm-key") options.llmKey = argv[++i]
    else if (arg === "--level") {
      options.level = argv[++i]
      if (!["low", "medium", "high"].includes(options.level)) {
        console.error(`openmagi: unknown level "${options.level}"; use low, medium or high`)
        process.exit(2)
      }
    }
    else if (arg === "--only") {
      options.only = argv[++i].split(",").map((s) => s.trim())
      const unknown = options.only.filter((id) => !HARNESSES.includes(id))
      // Silently installing nothing because of a typo is worse than refusing.
      if (unknown.length) {
        console.error(`openmagi: unknown harness ${unknown.join(", ")}\n  known: ${HARNESSES.join(", ")}`)
        process.exit(2)
      }
    }
    else positional.push(arg)
  }
  return { command: positional[0], rest: positional.slice(1), options }
}

const HARNESSES = ["opencode", "kilo", "opencode2", "pi", "codex", "dsh"]

const USAGE = `OPENMAGI — auto mode for open-source coding agents

  node adapters/packages/installer/bin/openmagi.js <command>
  after the first install the same CLI is on PATH as: openmagi <command>

  install [--only <harnesses>] [--level low|medium|high]

    point at a guard somebody already runs:
      install --guard-url URL --token T [--profile P]

    or start the bundled one (docker compose, needs an LLM for stage 2):
      install --start-guard --llm-url URL --llm-model NAME --llm-key KEY

    --level writes ~/.config/gate/rules.json: the deterministic allow/ask/deny
    groups every harness sends with every decision. Yours to edit afterwards;
    install never overwrites a file that is already there.

  status
  doctor
  mode <auto|ask|allow|off>
  uninstall [--only <harnesses>]

  harnesses: ${HARNESSES.join(", ")}
`

async function main() {
  const { command, rest, options } = parseArgs(process.argv.slice(2))

  switch (command) {
    case "install": {
      // The wordmark first. An install writes into other people's config, and
      // whoever runs it should see whose installer this is before it starts.
      // Ahead of the wizard, so backing out still leaves a screen that makes
      // sense.
      console.log(`\n${wordmark()}\n`)
      // The wizard asks only what the flags did not already answer, and returns
      // null when the user backs out — so nothing is touched on a cancel.
      const answers = await runWizard(options)
      if (!answers) break
      await install({
        ...options,
        only: answers.targets,
        guardUrl: answers.guardUrl ?? options.guardUrl,
        token: answers.token ?? options.token,
        startGuard: answers.startGuard,
        llmUrl: answers.llmUrl,
        llmModel: answers.llmModel,
        llmKey: answers.llmKey,
        level: answers.level,
      })
      break
    }
    case "uninstall":
      uninstall(options)
      break
    case "status": {
      const report = await status(options)
      console.log(`openmagi mode: ${report.mode}`)
      console.log(`guard: ${report.guardHealthy ? "reachable" : "unreachable"} (${report.guardUrl})`)
      for (const t of report.targets) {
        console.log(`  ${t.id} ${t.version}: ${t.installed ? "installed" : "not installed"} (${t.mode})`)
      }
      break
    }
    case "doctor": {
      const findings = await doctor(options)
      for (const f of findings) {
        const glyph = f.level === "ok" ? mark.ok() : f.level === "warn" ? mark.warn() : mark.bad()
        console.log(`${glyph} ${f.message}`)
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
  console.error(`openmagi: ${error.message}`)
  process.exit(1)
})
