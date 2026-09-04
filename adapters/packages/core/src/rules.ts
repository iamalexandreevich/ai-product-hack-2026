/**
 * The deterministic ruleset the adapter ships with every decision.
 *
 * Stage 1 of the guard is deterministic — no model, no latency. What it treats
 * as obviously fine, obviously forbidden, or worth a question is policy, and
 * policy the user should be able to see and edit. So it lives in one JSON file
 * (`~/.config/gate/rules.json`), is chosen from three levels at install time,
 * applies to every harness at once, and travels with the request.
 *
 * The file is the user's, not ours: it is written once at install and never
 * rewritten, so hand edits survive. `level: "custom"` is what an edited file
 * should say, and the installer will not touch a file that says so.
 */

import fsModule from "node:fs"

export type RuleLevel = "low" | "medium" | "high" | "custom"

/**
 * Three named groups, matched in order deny → ask → allow. Patterns are
 * glob-ish command prefixes and paths; the service decides how to match them,
 * the adapter only carries them.
 */
export type RuleSet = {
  /** Version of this file's own shape, so the service can reject what it cannot read. */
  version: 1
  level: RuleLevel
  /** Runs without asking. */
  allow: string[]
  /** Goes to the human. */
  ask: string[]
  /** Never runs. */
  deny: string[]
}

/** Always refused, at every level: no policy should make these negotiable. */
const ALWAYS_DENY = [
  "sudo *",
  "curl * | sh",
  "curl * | bash",
  "wget * | sh",
  "rm -rf /",
  "rm -rf ~",
  "chmod -R 777 *",
  "git push --force * main",
  "git push --force * master",
  ":(){ :|:& };:",
]

/** Read-only inspection, safe to run unattended at every level. */
const ALWAYS_ALLOW = [
  "git status",
  "git diff*",
  "git log*",
  "ls*",
  "cat *",
  "head *",
  "tail *",
  "grep *",
  "rg *",
  "find *",
  "pwd",
  "which *",
]

const SECRETS = ["**/.env", "**/.env.*", "~/.ssh/**", "~/.aws/**", "~/.kube/**", "**/id_rsa", "**/*.pem"]

const AGENT_CONFIG = ["**/.git/hooks/**", "**/.claude/**", "**/.codex/**", "**/.opencode/**", "**/AGENTS.md"]

/**
 * The three levels differ in one question: how much runs without asking.
 *
 * low     — only the obviously destructive is refused; everything else proceeds.
 * medium  — secrets and agent config are off limits; writes outside the
 *           workspace and package installs get a question.
 * high    — anything that leaves the workspace or reaches the network is asked
 *           about, and secrets are refused outright rather than questioned.
 */
export function rulesFor(level: Exclude<RuleLevel, "custom">): RuleSet {
  if (level === "low") {
    return {
      version: 1,
      level,
      allow: [...ALWAYS_ALLOW, "npm *", "pnpm *", "yarn *", "make *", "docker *", "git *"],
      ask: [...SECRETS],
      deny: [...ALWAYS_DENY],
    }
  }

  if (level === "high") {
    return {
      version: 1,
      level,
      allow: ALWAYS_ALLOW,
      ask: [
        "npm install*",
        "pnpm add*",
        "yarn add*",
        "pip install*",
        "git push*",
        "git reset --hard*",
        "docker *",
        "curl *",
        "wget *",
        "**/../**",
      ],
      deny: [...ALWAYS_DENY, ...SECRETS, ...AGENT_CONFIG, "rm -rf *"],
    }
  }

  return {
    version: 1,
    level: "medium",
    allow: [...ALWAYS_ALLOW, "npm test*", "npm run lint*", "pytest*", "cargo test*", "git add*", "git commit*"],
    ask: ["npm install*", "pnpm add*", "pip install*", "git push*", "curl *", "wget *", "rm -rf *"],
    deny: [...ALWAYS_DENY, ...SECRETS, ...AGENT_CONFIG],
  }
}

export function isRuleLevel(value: unknown): value is Exclude<RuleLevel, "custom"> {
  return value === "low" || value === "medium" || value === "high"
}

/** Parses a rules file, returning null when it is absent or unreadable. */
export function parseRules(text: string): RuleSet | null {
  try {
    const parsed = JSON.parse(text) as Partial<RuleSet>
    if (parsed.version !== 1) return null
    if (!Array.isArray(parsed.allow) || !Array.isArray(parsed.ask) || !Array.isArray(parsed.deny)) return null
    return {
      version: 1,
      level: (parsed.level as RuleLevel) ?? "custom",
      allow: parsed.allow,
      ask: parsed.ask,
      deny: parsed.deny,
    }
  } catch {
    return null
  }
}

/**
 * Reads the ruleset from disk, cached by mtime so an edit applies to the next
 * decision without restarting the agent — the same contract the mode has.
 * A missing or unreadable file means "no rules", not a failure: the service
 * falls back to its own profile, which is what happened before this existed.
 */
let cached: { mtimeMs: number; rules: RuleSet | null } | null = null

/** The slice of `node:fs` this module needs, so tests can hand it a fake. */
export type RulesFs = {
  statSync: (p: string) => { mtimeMs: number }
  readFileSync: (p: string, enc: "utf8") => string
}

const nodeFs = fsModule as unknown as RulesFs

export function readRules(rulesPath: string, fs: RulesFs = nodeFs): RuleSet | null {
  try {
    const stat = fs.statSync(rulesPath)
    if (cached && cached.mtimeMs === stat.mtimeMs) return cached.rules
    const rules = parseRules(fs.readFileSync(rulesPath, "utf8"))
    cached = { mtimeMs: stat.mtimeMs, rules }
    return rules
  } catch {
    cached = null
    return null
  }
}

export function clearRulesCache(): void {
  cached = null
}


