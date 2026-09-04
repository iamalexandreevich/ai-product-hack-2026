/**
 * Bringing up the guard service, and choosing the policy it enforces.
 *
 * Two ways to have a guard: point at one somebody already runs, or start the
 * local one. The first needs a URL and a credential; the second additionally
 * needs an LLM for stage 2 and a protection level for stage 1.
 *
 * The level is not a server setting — it writes `~/.config/gate/rules.json`,
 * which every harness sends with every decision. One file, all agents, editable
 * by hand.
 */
import fs from "node:fs"
import path from "node:path"
import { isRuleLevel, parseRules, rulesFor, type RuleLevel } from "../../core/src/rules.ts"
import { adaptersRoot } from "./bundle.ts"
import type { Runner } from "./harness-cli.ts"

export type ServiceOptions = {
  /** Where the compose file lives; defaults to the checkout's `service/`. */
  dir?: string
  token: string
  llmUrl?: string
  llmModel?: string
  llmKey?: string
  run: Runner
  log: (line: string) => void
}

/** `service/` sits beside `adapters/` in the repository. */
export function serviceDir(root = adaptersRoot()): string {
  return path.join(path.dirname(root), "service")
}

/**
 * Writes the ruleset unless the user already has one. Their file is never
 * overwritten — an edited policy surviving `install` is the whole point of it
 * being a file rather than a flag.
 */
export function writeRules(rulesPath: string, level: RuleLevel, log: (line: string) => void): boolean {
  if (fs.existsSync(rulesPath)) {
    const existing = parseRules(fs.readFileSync(rulesPath, "utf8"))
    if (existing) {
      log(`  rules: keeping your ${rulesPath} (level: ${existing.level})`)
      return false
    }
    log(`  rules: ${rulesPath} is not readable as a ruleset — leaving it alone`)
    return false
  }
  if (!isRuleLevel(level)) return false
  fs.mkdirSync(path.dirname(rulesPath), { recursive: true })
  fs.writeFileSync(rulesPath, JSON.stringify(rulesFor(level), null, 2) + "\n")
  log(`  rules: ${level} → ${rulesPath} (edit it freely; install will not overwrite)`)
  return true
}

/**
 * Starts the bundled guard with docker compose. The compose file refuses to run
 * without AGENTGATE_TOKEN, so it is generated here when the caller has none.
 */
export function startService(options: ServiceOptions): { ok: boolean; url: string } {
  const dir = options.dir ?? serviceDir()
  const url = "http://127.0.0.1:8400"
  if (!fs.existsSync(path.join(dir, "docker-compose.yml"))) {
    options.log(`  ! no docker-compose.yml in ${dir} — is this a full checkout?`)
    return { ok: false, url }
  }

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    AGENTGATE_TOKEN: options.token,
    OPENROUTER_API_KEY: options.llmKey ?? "",
    OPENROUTER_MODEL_NAME: options.llmModel ?? "",
  }
  if (options.llmUrl) env.OPENROUTER_BASE_URL = options.llmUrl

  options.log("  starting the guard (docker compose up -d --build) — first run builds the image")
  const result = options.run("docker", ["compose", "up", "-d", "--build"], { cwd: dir, env, timeoutMs: 900_000 })
  if (!result.ok) {
    options.log(`  ! docker compose failed: ${(result.stderr || result.stdout).trim().slice(0, 200)}`)
    return { ok: false, url }
  }
  if (!options.llmKey) {
    // Stage 1 still works; stage 2 fails closed to `ask`, which is safe but noisy.
    options.log("  ! no LLM key given — stage 2 will fail closed to `ask` until one is set")
  }
  return { ok: true, url }
}

/** Polls until the guard answers, so `install` can report a usable state. */
export async function waitForService(url: string, timeoutMs = 120_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/healthz`, { signal: AbortSignal.timeout(3000) })
      if (res.ok) {
        const body = (await res.json()) as { db?: boolean }
        if (body.db) return true
      }
    } catch {
      // Not up yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  return false
}
