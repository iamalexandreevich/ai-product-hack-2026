import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { hasComments, parseJsonc, removeTopLevelKey, setTopLevelKey, stripJsonc } from "../src/jsonc.ts"

describe("jsonc", () => {
  it("parses comments and trailing commas", () => {
    const text = `{
  // the schema
  "$schema": "https://x/config.json",
  /* block */
  "theme": "dark",
}`
    assert.deepEqual(parseJsonc(text), { $schema: "https://x/config.json", theme: "dark" })
  })

  it("does not treat comment markers inside strings as comments", () => {
    const text = '{"url": "https://example.com/a//b", "path": "/* not a comment */"}'
    assert.deepEqual(parseJsonc(text), { url: "https://example.com/a//b", path: "/* not a comment */" })
    assert.equal(hasComments(text), false)
  })

  it("adds a key to an empty object", () => {
    const out = setTopLevelKey('{\n}\n', "plugin", ["@agentgate/gate-plugin"])
    assert.deepEqual(parseJsonc(out), { plugin: ["@agentgate/gate-plugin"] })
  })

  it("keeps existing comments and keys byte-for-byte when adding", () => {
    const text = `{
  // keep me
  "$schema": "https://x/config.json"
}
`
    const out = setTopLevelKey(text, "plugin", ["a"])
    assert.match(out, /\/\/ keep me/)
    assert.match(out, /"\$schema": "https:\/\/x\/config\.json"/)
    assert.deepEqual(parseJsonc(out), { $schema: "https://x/config.json", plugin: ["a"] })
  })

  it("replaces an existing key without disturbing its neighbours", () => {
    const text = `{
  "$schema": "s",
  "plugin": ["old"],
  // trailing note
  "theme": "dark"
}
`
    const out = setTopLevelKey(text, "plugin", ["new"])
    assert.deepEqual(parseJsonc(out), { $schema: "s", plugin: ["new"], theme: "dark" })
    assert.match(out, /\/\/ trailing note/)
  })

  it("is idempotent: applying the same edit twice changes nothing", () => {
    const text = `{
  "$schema": "s"
}
`
    const once = setTopLevelKey(text, "plugin", ["a"])
    assert.equal(setTopLevelKey(once, "plugin", ["a"]), once)
  })

  it("removes a key and leaves valid JSON", () => {
    const text = `{
  "$schema": "s",
  "plugin": ["a"],
  "theme": "dark"
}
`
    const out = removeTopLevelKey(text, "plugin")
    assert.deepEqual(parseJsonc(out), { $schema: "s", theme: "dark" })
  })

  it("removes the last key without leaving a dangling comma", () => {
    const text = `{
  "$schema": "s",
  "plugin": ["a"]
}
`
    const out = removeTopLevelKey(text, "plugin")
    assert.deepEqual(parseJsonc(out), { $schema: "s" })
  })

  it("handles nested objects that contain the same key name", () => {
    const text = `{
  "provider": { "x": { "plugin": "inner" } },
  "plugin": ["outer"]
}
`
    const out = setTopLevelKey(text, "plugin", ["changed"])
    const parsed = parseJsonc(out)
    assert.deepEqual(parsed.plugin, ["changed"])
    assert.deepEqual(parsed.provider.x.plugin, "inner")
  })

  it("strips comments without changing string content", () => {
    assert.equal(stripJsonc('{"a": "b"} // tail').trim(), '{"a": "b"}')
  })
})
