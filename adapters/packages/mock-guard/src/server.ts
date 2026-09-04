#!/usr/bin/env node
/**
 * Mock guard service.
 *
 * Implements `POST /v1/decide` exactly as openapi.yaml describes it, plus the
 * provisional `POST /v1/inspect` the adapters need for tool results. The rules
 * are crude regexes on purpose: this stands in for the real classifier so the
 * integration can be tested and demoed without it, and so failure modes
 * (slow guard, broken guard, absent guard) can be produced on demand.
 *
 *   node server.ts --port 8400 [--delay 500] [--fail] [--token T]
 *
 * `GET /v1/decisions` returns everything it was asked, which is how a test
 * asserts that every single tool call actually reached the guard. Response shapes
 * follow contracts/openapi.yaml so the mock can be swapped for the real service.
 */
import http from "node:http"
import { randomUUID } from "node:crypto"

type Json = Record<string, any>

const argv = process.argv.slice(2)
const flag = (name: string, fallback?: string): string | undefined => {
  const at = argv.indexOf(`--${name}`)
  return at === -1 ? fallback : argv[at + 1]
}
const has = (name: string): boolean => argv.includes(`--${name}`)

const PORT = Number(flag("port", "8400"))
const DELAY = Number(flag("delay", "0"))
const TOKEN = flag("token")
const ALWAYS_FAIL = has("fail")

/** Patterns that are never acceptable, whatever the task is. */
const HARD_DENY: Array<{ id: string; re: RegExp; reason: string; suggest: string }> = [
  {
    id: "hard-deny.pipe-exec",
    re: /\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|python3?|node)\b/i,
    reason: "piping a downloaded script straight into a shell executes unreviewed remote code",
    suggest: "Download the script to a file, read it, then run it deliberately.",
  },
  {
    id: "hard-deny.destructive",
    re: /\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b/i,
    reason: "recursive force delete can destroy work that is not recoverable",
    suggest: "Delete a specific path, or move it to a temporary directory first.",
  },
  {
    id: "hard-deny.exfil",
    re: /(\.env\b|id_rsa|\.ssh\/|\.aws\/|\.kube\/|\.pem\b|\.p12\b)/i,
    reason: "the command touches credential material",
    suggest: "Use an environment variable supplied by the operator instead of reading the secret.",
  },
  {
    id: "hard-deny.privilege",
    re: /\b(sudo|su)\b|\bchmod\s+777\b|\bchown\b/i,
    reason: "privilege escalation is outside what an agent may do unattended",
    suggest: "Ask the operator to run the privileged step.",
  },
  {
    id: "hard-deny.git-force",
    re: /\bgit\s+push\b.*(--force\b|-f\b)/i,
    reason: "a force push can discard commits on a shared branch",
    suggest: "Push normally, or force-push to a personal branch.",
  },
]

/** Read-only commands that never need a human. */
const ALLOWLIST =
  /^\s*(git\s+(status|diff|log|show|branch)|ls|pwd|cat|head|tail|wc|grep|rg|find|which|echo|node\s+--version|npm\s+(ls|view))\b/i

/** Text that is trying to talk to the model rather than inform it. */
const INJECTION = [
  /ignore\s+(all\s+)?previous\s+instructions/i,
  /disregard\s+(all\s+)?(prior|previous)\s+instructions/i,
  /you\s+are\s+now\s+in\s+\w+\s+mode/i,
  /do\s+not\s+mention\s+this\s+to\s+the\s+user/i,
  /\b(curl|wget)\b[^|\n]*\|\s*(sh|bash)\b/i,
]

const decisions: Json[] = []

function ulid(): string {
  return `01J${randomUUID().replace(/-/g, "").slice(0, 23).toUpperCase()}`
}

function decide(body: Json): Json {
  const started = Date.now()
  const raw: string = String(body.raw ?? "")
  const paths: string[] = Array.isArray(body.args?.paths) ? body.args.paths : []
  const haystack = [raw, ...paths].join(" ")

  const answer = (
    decision: string,
    reason: string,
    suggest: string,
    stage: number,
    ruleId: string | null,
  ): Json => ({
    decision,
    reason,
    suggest,
    stage,
    rule_id: ruleId,
    model: stage === 2 ? "mock-classifier" : null,
    latency_ms: { stage1: 1, stage2: stage === 2 ? Date.now() - started : null, total: Date.now() - started },
    cached: false,
    decision_id: ulid(),
  })

  for (const rule of HARD_DENY) {
    if (rule.re.test(haystack)) return answer("deny", rule.reason, rule.suggest, 1, rule.id)
  }

  if (body.tool === "shell" && ALLOWLIST.test(raw)) {
    return answer("allow", "", "", 1, "allowlist.readonly")
  }

  if (body.tool === "file_read") {
    return answer("allow", "", "", 1, "allowlist.readonly")
  }

  if (body.tool === "network") {
    return answer(
      "ask",
      `network access to ${body.args?.domains?.[0] ?? raw} is not on the allowlist`,
      "Confirm the destination is expected for this task.",
      1,
      "profile.network",
    )
  }

  // Anything the deterministic rules did not settle is what stage 2 would judge.
  return answer(
    "ask",
    "not covered by a deterministic rule; a human should confirm this one",
    "",
    2,
    null,
  )
}

function inspect(body: Json): Json {
  const started = Date.now()
  const output = String(body.output ?? "")
  const lines = output.split("\n")
  const flagged = lines.filter((line) => INJECTION.some((re) => re.test(line)))

  const base = {
    stage: 1,
    rule_id: flagged.length ? "inspect.injection" : null,
    model: null,
    latency_ms: { stage1: Date.now() - started, stage2: null, total: Date.now() - started },
    cached: false,
    decision_id: ulid(),
  }

  if (!flagged.length) return { verdict: "pass", reason: "", ...base }

  // More than half the payload is hostile: nothing worth keeping.
  if (flagged.length > lines.length / 2) {
    return {
      verdict: "drop",
      reason: `prompt injection detected in ${flagged.length} of ${lines.length} lines`,
      ...base,
    }
  }

  const masked = lines
    .map((line) => (INJECTION.some((re) => re.test(line)) ? "[gate: instruction-like text removed]" : line))
    .join("\n")

  return {
    verdict: "mask",
    output: masked,
    reason: `removed ${flagged.length} line(s) that tried to instruct the model`,
    ...base,
  }
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost")

  const send = (status: number, payload: unknown): void => {
    res.writeHead(status, { "content-type": "application/json" })
    res.end(JSON.stringify(payload))
  }

  if (req.method === "GET" && url.pathname === "/healthz") {
    // Shape fixed by contracts/openapi.yaml (Health): additionalProperties: false,
    // `db` is the boolean probe result, `llm` is reserved and currently always null.
    return send(200, { status: "ok", db: true, llm: null })
  }

  if (req.method === "GET" && url.pathname === "/v1/decisions") {
    // Shape fixed by contracts/openapi.yaml (DecisionListResponse): `items` newest
    // first plus a `next_before` cursor. The real service pages by decision_id; the
    // mock keeps everything in memory and always reports the last page.
    const limit = Number(url.searchParams.get("limit") ?? "100")
    return send(200, { items: decisions.slice(-limit).reverse(), next_before: null })
  }

  if (req.method === "DELETE" && url.pathname === "/v1/decisions") {
    decisions.length = 0
    return send(200, { ok: true })
  }

  if (req.method !== "POST") return send(404, { error: "not found" })

  let raw = ""
  req.on("data", (chunk) => {
    raw += chunk
  })
  req.on("end", async () => {
    // 401 is the only non-200 the contract allows on purpose.
    if (TOKEN && req.headers.authorization !== `Bearer ${TOKEN}`) {
      return send(401, { error: "unauthorized" })
    }
    if (ALWAYS_FAIL) return send(500, { error: "mock guard failing on purpose" })
    if (DELAY > 0) await new Promise((resolve) => setTimeout(resolve, DELAY))

    let body: Json = {}
    try {
      body = JSON.parse(raw || "{}")
    } catch {
      // Per the contract, an invalid body is still HTTP 200 with `ask`.
      return send(200, {
        decision: "ask",
        reason: "invalid request body",
        suggest: "",
        stage: 0,
        rule_id: null,
        model: null,
        latency_ms: { stage1: null, stage2: null, total: 0 },
        cached: false,
        decision_id: ulid(),
      })
    }

    const direction = url.pathname === "/v1/inspect" ? "in" : "out"
    const answer = direction === "in" ? inspect(body) : decide(body)
    decisions.push({
      direction,
      at: new Date().toISOString(),
      idempotency_key: req.headers["idempotency-key"] ?? null,
      request: body,
      response: answer,
    })
    process.stderr.write(
      `[mock-guard] ${direction} ${body.tool ?? "?"} ${JSON.stringify(body.raw ?? body.output ?? "").slice(0, 60)} -> ${
        (answer as Json).decision ?? (answer as Json).verdict
      }\n`,
    )
    send(200, answer)
  })
})

server.listen(PORT, "127.0.0.1", () => {
  process.stderr.write(
    `[mock-guard] listening on http://127.0.0.1:${PORT} delay=${DELAY}ms fail=${ALWAYS_FAIL} auth=${Boolean(TOKEN)}\n`,
  )
})
