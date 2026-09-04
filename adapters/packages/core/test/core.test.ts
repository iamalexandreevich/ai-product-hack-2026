import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { after, describe, it } from "node:test"

import {
  InspectCache,
  LIMITS,
  VerdictBook,
  buildDecideRequest,
  byteLength,
  clampMetadata,
  clampRawBytes,
  clampUserRequest,
  cycle,
  denyMessage,
  endpoint,
  idempotencyKey,
  loadConfig,
  mapPermission,
  mapToolCall,
  readMode,
  resolveIn,
  resolveOut,
  writeMode,
} from "../src/index.ts"

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "gate-test-"))
after(() => fs.rmSync(tmp, { recursive: true, force: true }))

describe("tool mapping", () => {
  it("maps bash to shell with the command as raw", () => {
    const mapped = mapToolCall("bash", { command: "rm -rf /tmp/x" }, "/repo")
    assert.equal(mapped.tool, "shell")
    assert.equal(mapped.raw, "rm -rf /tmp/x")
    assert.deepEqual(mapped.provenance, { kind: "shell", command: "rm -rf /tmp/x" })
  })

  it("resolves relative file paths against cwd", () => {
    const mapped = mapToolCall("read", { filePath: "docs/README.md" }, "/repo")
    assert.equal(mapped.tool, "file_read")
    assert.deepEqual(mapped.args.paths, ["/repo/docs/README.md"])
  })

  it("extracts the host for webfetch so the service sees a domain", () => {
    const mapped = mapToolCall("webfetch", { url: "https://evil.example/x?y=1" }, "/repo")
    assert.equal(mapped.tool, "network")
    assert.deepEqual(mapped.args.domains, ["evil.example"])
  })

  it("keeps the subagent prompt as the classifiable text", () => {
    const mapped = mapToolCall("task", { prompt: "delete everything", subagent_type: "general" }, "/repo")
    assert.equal(mapped.raw, "delete everything")
    assert.equal(mapped.args.mcp?.server, "subagent")
    assert.equal(mapped.provenance.kind, "subagent")
  })

  it("splits MCP tool names into server and tool", () => {
    const mapped = mapToolCall("github_create_issue", { title: "t" }, "/repo")
    assert.equal(mapped.tool, "mcp_call")
    assert.deepEqual(mapped.args.mcp?.server, "github")
    assert.deepEqual(mapped.args.mcp?.tool, "create_issue")
  })

  it("reads external_directory permissions as a file or a command", () => {
    const asFile = mapPermission("external_directory", { filepath: "/tmp/d/README.md" }, "/repo")
    assert.equal(asFile.tool, "file_read")
    const asCommand = mapPermission("external_directory", { command: "rm -rf /tmp/d" }, "/repo")
    assert.equal(asCommand.tool, "shell")
    assert.equal(asCommand.raw, "rm -rf /tmp/d")
  })
})

describe("request limits", () => {
  it("counts raw in bytes, not characters", () => {
    // 20000 Cyrillic characters is 40000 bytes and already over the limit.
    const cyrillic = "д".repeat(20000)
    assert.ok(byteLength(cyrillic) > LIMITS.rawBytes)
    assert.ok(byteLength(clampRawBytes(cyrillic)) <= LIMITS.rawBytes)
  })

  it("keeps the tail of user_request, because that is the current task", () => {
    assert.equal(clampUserRequest("abcdefghij", 4), "ghij")
  })

  it("drops metadata keys until it fits", () => {
    const clamped = clampMetadata({ keep: "x", huge: "y".repeat(20000) }, 100)
    assert.deepEqual(Object.keys(clamped), ["keep"])
  })

  it("produces a request that satisfies the contract's required fields", () => {
    const action = mapToolCall("bash", { command: "ls" }, "/repo")
    const body = buildDecideRequest(action, {
      harness: { name: "opencode", version: "1.17.18", patched: false },
      sessionId: "ses_1",
      callId: "call_1",
      userRequest: "list files",
      mode: "auto",
    })
    for (const field of ["harness", "tool", "args", "user_request"]) {
      assert.ok(field in body, `missing required field ${field}`)
    }
    assert.ok(body.args.cwd.length > 0)
    assert.equal(body.metadata?.tool_name, "bash")
  })
})

describe("mode file", () => {
  it("cycles the three working modes and never lands on off", () => {
    assert.equal(cycle("auto"), "ask")
    assert.equal(cycle("ask"), "allow")
    assert.equal(cycle("allow"), "auto")
    assert.notEqual(cycle("allow"), "off")
  })

  it("defaults to auto when no state file exists", () => {
    assert.equal(readMode(path.join(tmp, "missing.json")), "auto")
  })

  it("round-trips through the file and sees a later write", () => {
    const statePath = path.join(tmp, "state.json")
    writeMode(statePath, "allow")
    assert.equal(readMode(statePath), "allow")
    writeMode(statePath, "deny" as never)
    writeMode(statePath, "ask")
    assert.equal(readMode(statePath), "ask")
  })
})

describe("policy", () => {
  const answer = (decision: string) => ({
    ok: true as const,
    value: {
      decision,
      reason: "because",
      suggest: "do X",
      stage: 1,
      rule_id: "hard-deny.destructive",
      model: null,
      latency_ms: { stage1: 1, stage2: null, total: 1 },
      cached: false,
      decision_id: "01J",
    },
  })
  const failure = (kind: "unreachable" | "refused" | "malformed") => ({
    ok: false as const,
    failure: { kind, detail: "boom" },
  })

  it("passes the guard verdict through in auto mode", () => {
    assert.equal(resolveOut("auto", "ask", answer("deny") as any, "allow").status, "deny")
    assert.equal(resolveOut("auto", "ask", answer("allow") as any, "allow").status, "allow")
  })

  it("escalates a call the rules would have allowed", () => {
    assert.equal(resolveOut("auto", "allow", answer("ask") as any, "allow").status, "ask")
  })

  it("does not consult the guard in ask and allow modes", () => {
    assert.equal(resolveOut("ask", "allow", null, "allow").status, "ask")
    assert.equal(resolveOut("allow", "ask", null, "allow").status, "allow")
  })

  it("leaves the harness alone in off mode", () => {
    assert.equal(resolveOut("off", "ask", answer("allow") as any, "allow").source, "rules")
  })

  it("never turns a bad answer into allow, even when failing open", () => {
    // The contract is explicit: a reachable-but-unhappy service means ask.
    assert.equal(resolveOut("auto", "allow", failure("refused") as any, "allow").status, "ask")
    assert.equal(resolveOut("auto", "allow", failure("malformed") as any, "allow").status, "ask")
  })

  it("applies on_unavailable only when the guard is truly absent", () => {
    assert.equal(resolveOut("auto", "ask", failure("unreachable") as any, "deny").status, "deny")
    assert.equal(resolveOut("auto", "allow", failure("unreachable") as any, "allow").status, "allow")
  })

  it("renders a deny message the model can act on", () => {
    const text = denyMessage(resolveOut("auto", "ask", answer("deny") as any, "allow"))
    assert.match(text, /hard-deny\.destructive/)
    assert.match(text, /because/)
    assert.match(text, /do X/)
  })

  it("replaces output on mask and drop, passes otherwise", () => {
    const inspect = (verdict: string, output?: string) => ({
      ok: true as const,
      value: {
        verdict,
        output,
        reason: "injection",
        stage: 1,
        rule_id: null,
        model: null,
        latency_ms: { stage1: 1, stage2: null, total: 1 },
        cached: false,
        decision_id: "01J",
      },
    })
    assert.equal(resolveIn("auto", inspect("pass") as any, "text", "allow").action, "pass")
    const masked = resolveIn("auto", inspect("mask", "clean") as any, "text", "allow")
    assert.equal(masked.action === "replace" && masked.output, "clean")
    const dropped = resolveIn("auto", inspect("drop") as any, "text", "allow")
    assert.ok(dropped.action === "replace" && dropped.output.startsWith("[gate] result removed"))
  })

  it("keeps the original when mask arrives without replacement text", () => {
    const noText = {
      ok: true as const,
      value: {
        verdict: "mask",
        reason: "",
        stage: 1,
        rule_id: null,
        model: null,
        latency_ms: { stage1: 1, stage2: null, total: 1 },
        cached: false,
        decision_id: "01J",
      },
    }
    assert.equal(resolveIn("auto", noText as any, "original", "allow").action, "pass")
  })
})

describe("caching and idempotency", () => {
  it("keys the inspect cache by content, so identical text is free", () => {
    const cache = new InspectCache()
    const value = { verdict: "pass" } as any
    cache.set("same text", value)
    assert.equal(cache.get("same text"), value)
    assert.equal(cache.get("other text"), undefined)
  })

  it("hands a verdict from the before-hook to the event handler exactly once", () => {
    const book = new VerdictBook<string>()
    book.set("call_1", "allow")
    assert.equal(book.peek("call_1"), "allow")
    assert.equal(book.take("call_1"), "allow")
    assert.equal(book.take("call_1"), undefined)
  })

  it("produces a stable key per (harness, session, call, direction)", () => {
    const a = idempotencyKey("opencode", "ses_1", "call_1", "out")
    assert.equal(a, idempotencyKey("opencode", "ses_1", "call_1", "out"))
    assert.notEqual(a, idempotencyKey("opencode", "ses_1", "call_1", "in"))
    assert.notEqual(a, idempotencyKey("kilo", "ses_1", "call_1", "out"))
  })
})

describe("config", () => {
  it("defaults to a local guard and fail-open", () => {
    const config = loadConfig({}, {})
    assert.equal(endpoint(config, "decide"), "http://127.0.0.1:8400/v1/decide")
    assert.equal(config.onUnavailable, "allow")
  })

  it("honours GATE_FAIL_CLOSED and the finer on_unavailable", () => {
    assert.equal(loadConfig({}, { GATE_FAIL_CLOSED: "1" }).onUnavailable, "deny")
    assert.equal(loadConfig({}, { GATE_ON_UNAVAILABLE: "ask" }).onUnavailable, "ask")
  })

  it("lets plugin options win over the environment", () => {
    const config = loadConfig({ url: "http://opt:1" }, { AGENTGATE_URL: "http://env:2" })
    assert.equal(config.url, "http://opt:1")
  })
})

describe("opencode 2.0 tool vocabulary", () => {
  it("maps the v2 shell tool the same as v1 bash", () => {
    // opencode 2.0 renamed bash -> shell (and also exposes execute).
    for (const name of ["shell", "execute"]) {
      const mapped = mapToolCall(name, { command: "rm -rf /tmp/x" }, "/repo")
      assert.equal(mapped.tool, "shell")
      assert.equal(mapped.raw, "rm -rf /tmp/x")
    }
  })

  it("reads the v2 file path key `path` as well as v1 `filePath`", () => {
    const v2 = mapToolCall("read", { path: "/repo/a.md" }, "/repo")
    assert.deepEqual(v2.args.paths, ["/repo/a.md"])
    const v1 = mapToolCall("read", { filePath: "/repo/a.md" }, "/repo")
    assert.deepEqual(v1.args.paths, ["/repo/a.md"])
  })

  it("maps the v2 write tool by `path`", () => {
    const mapped = mapToolCall("write", { path: "/repo/out.txt", content: "x" }, "/repo")
    assert.equal(mapped.tool, "file_write")
    assert.deepEqual(mapped.args.paths, ["/repo/out.txt"])
  })
})
