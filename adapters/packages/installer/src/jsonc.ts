/**
 * Minimal JSONC handling for editing harness configs in place.
 *
 * The harnesses accept `.jsonc`, and users keep comments there. Rewriting the
 * file from a parsed object would silently delete them, so edits are surgical:
 * a top-level key is inserted or replaced by splicing text, and every other
 * byte of the file is left exactly as it was. Only a file with no comments at
 * all is safe to reformat wholesale, and even then it is not.
 */

/** Strips comments and trailing commas so JSON.parse can read a JSONC file. */
export function stripJsonc(text: string): string {
  let out = ""
  let inString = false
  let inLine = false
  let inBlock = false
  let escaped = false

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    const next = text[i + 1]

    if (inLine) {
      if (char === "\n") {
        inLine = false
        out += char
      }
      continue
    }
    if (inBlock) {
      if (char === "*" && next === "/") {
        inBlock = false
        i += 1
      }
      continue
    }
    if (inString) {
      out += char
      if (escaped) escaped = false
      else if (char === "\\") escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') {
      inString = true
      out += char
      continue
    }
    if (char === "/" && next === "/") {
      inLine = true
      i += 1
      continue
    }
    if (char === "/" && next === "*") {
      inBlock = true
      i += 1
      continue
    }
    out += char
  }

  // Trailing commas are legal in JSONC and fatal to JSON.parse.
  return out.replace(/,(\s*[}\]])/g, "$1")
}

export function parseJsonc(text: string): Record<string, any> {
  const trimmed = text.trim()
  if (!trimmed) return {}
  return JSON.parse(stripJsonc(text))
}

export function hasComments(text: string): boolean {
  return stripJsonc(text).replace(/\s/g, "") !== text.replace(/\s/g, "")
}

/** Locates a top-level key's `"key": value` span, brace- and string-aware. */
function findTopLevelKey(text: string, key: string): { start: number; end: number } | null {
  const needle = `"${key}"`
  let depth = 0
  let inString = false
  let escaped = false

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (char === "\\") escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') {
      if (depth === 1 && text.startsWith(needle, i)) {
        const start = i
        let cursor = i + needle.length
        while (cursor < text.length && text[cursor] !== ":") cursor += 1
        cursor += 1
        let valueDepth = 0
        let valueString = false
        let valueEscaped = false
        for (; cursor < text.length; cursor += 1) {
          const c = text[cursor]
          if (valueString) {
            if (valueEscaped) valueEscaped = false
            else if (c === "\\") valueEscaped = true
            else if (c === '"') valueString = false
            continue
          }
          if (c === '"') valueString = true
          else if (c === "{" || c === "[") valueDepth += 1
          else if (c === "}" || c === "]") {
            if (valueDepth === 0) break
            valueDepth -= 1
          } else if (c === "," && valueDepth === 0) break
        }
        // The scan stops on the delimiter, which may sit after a newline that
        // belongs to the file's formatting, not to the value. Give it back.
        let end = cursor
        while (end > start && /\s/.test(text[end - 1])) end -= 1
        return { start, end }
      }
      inString = true
      continue
    }
    if (char === "{" || char === "[") depth += 1
    else if (char === "}" || char === "]") depth -= 1
  }
  return null
}

/**
 * Sets one top-level key, keeping every comment and every other key byte-exact.
 */
export function setTopLevelKey(text: string, key: string, value: unknown): string {
  const source = text.trim() ? text : "{}\n"
  const rendered = JSON.stringify(value, null, 2)
    .split("\n")
    .map((line, index) => (index === 0 ? line : `  ${line}`))
    .join("\n")
  const entry = `"${key}": ${rendered}`

  const found = findTopLevelKey(source, key)
  if (found) return source.slice(0, found.start) + entry + source.slice(found.end)

  const close = source.lastIndexOf("}")
  if (close === -1) throw new Error("config is not a JSON object")
  const head = source.slice(0, close)
  const tail = source.slice(close)
  const body = head.replace(/\s+$/, "")
  // An empty object needs no separating comma; anything else does.
  const separator = /\{\s*$/.test(body) ? "" : ","
  return `${body}${separator}\n  ${entry}\n${tail}`
}

export function removeTopLevelKey(text: string, key: string): string {
  const found = findTopLevelKey(text, key)
  if (!found) return text
  let start = found.start
  let end = found.end
  // Swallow one adjoining comma so the object stays valid.
  if (text[end] === ",") end += 1
  else {
    const before = text.slice(0, start).replace(/\s+$/, "")
    if (before.endsWith(",")) start = before.length - 1
  }
  return text.slice(0, start).replace(/[ \t]+$/, "") + text.slice(end)
}
