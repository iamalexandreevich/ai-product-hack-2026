import { strict as assert } from "node:assert"
import { describe, it } from "node:test"
import { History, buildDecideRequest, clampHistory, mapToolCall } from "../src/index.ts"

const call = (command: string) => mapToolCall("bash", { command }, "/repo")

const context = (history: any) => ({
  harness: { name: "kilo", version: "7.5.6", patched: true },
  sessionId: "s1",
  callId: "c1",
  userRequest: "почини сборку",
  mode: "auto" as const,
  history,
})

describe("dialogue history", () => {
  it("keeps turns oldest first, so the guard reads the session in order", () => {
    const history = new History()
    history.record("s1", { role: "human", author: "human", content: "первое" })
    history.record("s1", { role: "assistant", author: "agent", content: "второе" })
    history.recordToolCall("s1", "bash", "c1", "npm test")

    assert.deepEqual(
      history.forRequest("s1").map((turn) => turn.content),
      ["первое", "второе", "npm test"],
    )
  })

  it("pairs a tool result with the call that produced it", () => {
    const history = new History()
    history.recordToolCall("s1", "bash", "call-7", "cat .env")
    history.recordToolResult("s1", "bash", "call-7", "SECRET=1")

    const [outgoing, incoming] = history.forRequest("s1")
    assert.equal(outgoing.role, "toolcall")
    assert.equal(outgoing.author, "agent")
    assert.equal(incoming.role, "toolresult")
    assert.equal(incoming.author, "system")
    assert.equal(outgoing.call_id, incoming.call_id)
  })

  it("keeps sessions apart", () => {
    const history = new History()
    history.record("s1", { role: "human", author: "human", content: "мой" })
    history.record("s2", { role: "human", author: "human", content: "чужой" })

    assert.deepEqual(history.forRequest("s1").map((t) => t.content), ["мой"])
    assert.deepEqual(history.forRequest("s2").map((t) => t.content), ["чужой"])
  })

  it("ignores a repeated turn: several hooks see the same message", () => {
    const history = new History()
    history.record("s1", { role: "human", author: "human", content: "раз" }, "msg-1")
    history.record("s1", { role: "human", author: "human", content: "раз" }, "msg-1")

    assert.equal(history.forRequest("s1").length, 1)
  })

  it("drops nothing but silence: a blank turn is never recorded", () => {
    const history = new History()
    history.record("s1", { role: "assistant", author: "agent", content: "   " })
    assert.equal(history.forRequest("s1").length, 0)
  })

  it("re-attributes a subagent's human turn to the agent that wrote it", () => {
    const history = new History()
    history.record("sub", { role: "human", author: "human", content: "найди утечки" })
    history.attributeTo("sub", "agent")

    const [turn] = history.forRequest("sub")
    assert.equal(turn.role, "human")
    assert.equal(turn.author, "agent")
  })

  it("reports the last human turn, skipping what the agent said after it", () => {
    const history = new History()
    history.record("s1", { role: "human", author: "human", content: "первая просьба" })
    history.record("s1", { role: "human", author: "human", content: "вторая просьба" })
    history.record("s1", { role: "assistant", author: "agent", content: "сейчас сделаю" })

    assert.equal(history.lastHumanRequest("s1"), "вторая просьба")
  })

  it("keeps the newest turns when the count limit is hit", () => {
    const history = new History(3)
    for (const n of [1, 2, 3, 4, 5]) {
      history.record("s1", { role: "assistant", author: "agent", content: `ход ${n}` })
    }
    assert.deepEqual(history.forRequest("s1").map((t) => t.content), ["ход 3", "ход 4", "ход 5"])
  })

  it("drops whole turns, never half of one, when over the byte budget", () => {
    const turns = Array.from({ length: 20 }, (_, n) => ({
      role: "assistant" as const,
      author: "agent" as const,
      content: `${n}`.padStart(3, "x").repeat(167),
    }))
    const kept = clampHistory(turns, { turns: 200, bytes: 3000 })

    assert.ok(kept.length < turns.length, "должно обрезать")
    assert.ok(kept.every((t) => t.content.length === 501), "каждый ход целый")
    assert.ok(kept.every((t) => turns.some((original) => original.content === t.content)))
    // The newest turn is the one that explains the action under review.
    assert.equal(kept.at(-1)?.content, turns.at(-1)?.content)
    assert.ok(Buffer.byteLength(JSON.stringify(kept)) <= 3000)
  })

  it("measures the budget in bytes, not characters", () => {
    const cyrillic = { role: "human" as const, author: "human" as const, content: "я".repeat(100) }
    const kept = clampHistory([cyrillic, cyrillic], { turns: 200, bytes: 250 })
    assert.equal(kept.length, 1)
  })

  it("puts history on the wire with a protocol marker", () => {
    const history = new History()
    history.record("s1", { role: "human", author: "human", content: "почини сборку" })
    const body = buildDecideRequest(call("rm -rf ./dist"), context(history.forRequest("s1")))

    assert.equal(body.protocol, 1)
    assert.deepEqual(body.history, [{ role: "human", author: "human", content: "почини сборку" }])
  })

  it("omits history and protocol entirely when there are no turns", () => {
    const body = buildDecideRequest(call("ls"), context([]))
    assert.ok(!("history" in body), "пустой список не отправляется")
    assert.ok(!("protocol" in body), "и версия протокола вместе с ним")
  })
})
