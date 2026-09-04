/**
 * Terminal prompts, built on nothing but node.
 *
 * The installer asks rather than assumes: which agents, which guard, how
 * strict. Every question can also be answered by a flag, and a non-interactive
 * shell (CI, a pipe, `--yes`) skips straight to the defaults — a prompt nobody
 * can answer is worse than no prompt.
 */
import readline from "node:readline/promises"

const ESC = "\x1b"
const dim = (s: string) => `${ESC}[2m${s}${ESC}[0m`
const bold = (s: string) => `${ESC}[1m${s}${ESC}[0m`
const cyan = (s: string) => `${ESC}[36m${s}${ESC}[0m`
const green = (s: string) => `${ESC}[32m${s}${ESC}[0m`

export type Choice<T> = { value: T; label: string; hint?: string }

export function isInteractive(): boolean {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY)
}

function render(lines: string[]): void {
  process.stdout.write(lines.join("\n") + "\n")
}

/** Moves the cursor back over what we drew, so a redraw replaces it. */
function clear(count: number): void {
  process.stdout.write(`${ESC}[${count}A${ESC}[0J`)
}

async function keyLoop(draw: () => string[], onKey: (key: string) => boolean): Promise<void> {
  const lines = draw()
  render(lines)
  let drawn = lines.length

  process.stdin.setRawMode(true)
  process.stdin.resume()
  process.stdin.setEncoding("utf8")

  await new Promise<void>((resolve) => {
    const handler = (chunk: string) => {
      // Ctrl+C must still work while the terminal is in raw mode.
      if (chunk === "") {
        process.stdin.off("data", handler)
        process.stdin.setRawMode(false)
        process.stdout.write("\n")
        process.exit(130)
      }
      const done = onKey(chunk)
      clear(drawn)
      const next = draw()
      render(next)
      drawn = next.length
      if (done) {
        process.stdin.off("data", handler)
        process.stdin.setRawMode(false)
        process.stdin.pause()
        resolve()
      }
    }
    process.stdin.on("data", handler)
  })
}

/** One of several. Arrows to move, Enter to take it. */
export async function select<T>(title: string, choices: Choice<T>[], initial = 0): Promise<T> {
  let cursor = initial
  let done = false

  await keyLoop(
    () => [
      `${cyan("◆")} ${bold(title)}`,
      ...choices.map((choice, i) => {
        const mark = i === cursor ? green("❯") : " "
        const label = i === cursor ? bold(choice.label) : choice.label
        return `  ${mark} ${label}${choice.hint ? "  " + dim(choice.hint) : ""}`
      }),
      dim("  ↑↓ выбрать · Enter подтвердить"),
    ],
    (key) => {
      if (key === "[A" || key === "k") cursor = (cursor - 1 + choices.length) % choices.length
      else if (key === "[B" || key === "j") cursor = (cursor + 1) % choices.length
      else if (key === "\r" || key === "\n") done = true
      return done
    },
  )
  return choices[cursor].value
}

/** Several of many. Space toggles, `a` takes all, Enter confirms. */
export async function multiSelect<T>(
  title: string,
  choices: Choice<T>[],
  preselected = true,
): Promise<T[]> {
  const picked = new Set<number>(preselected ? choices.map((_, i) => i) : [])
  let cursor = 0
  let done = false

  await keyLoop(
    () => [
      `${cyan("◆")} ${bold(title)}`,
      ...choices.map((choice, i) => {
        const mark = i === cursor ? green("❯") : " "
        const box = picked.has(i) ? green("◉") : "◯"
        return `  ${mark} ${box} ${choice.label}${choice.hint ? "  " + dim(choice.hint) : ""}`
      }),
      dim("  ↑↓ выбрать · Space отметить · a все · Enter подтвердить"),
    ],
    (key) => {
      if (key === "[A" || key === "k") cursor = (cursor - 1 + choices.length) % choices.length
      else if (key === "[B" || key === "j") cursor = (cursor + 1) % choices.length
      else if (key === " ") picked.has(cursor) ? picked.delete(cursor) : picked.add(cursor)
      else if (key === "a") {
        if (picked.size === choices.length) picked.clear()
        else choices.forEach((_, i) => picked.add(i))
      } else if (key === "\r" || key === "\n") done = picked.size > 0
      return done
    },
  )
  return choices.filter((_, i) => picked.has(i)).map((c) => c.value)
}

/** Free text. `secret` reads without echoing, so a key never reaches scrollback. */
export async function ask(question: string, opts: { default?: string; secret?: boolean } = {}): Promise<string> {
  if (opts.secret) return askSecret(question)
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  const suffix = opts.default ? dim(` (${opts.default})`) : ""
  try {
    const answer = await rl.question(`${cyan("◆")} ${bold(question)}${suffix}\n  `)
    return answer.trim() || opts.default || ""
  } finally {
    rl.close()
  }
}

/**
 * Reads a secret straight off the tty. readline echoes whatever it receives and
 * the documented ways to silence it are private API, so the characters are
 * collected by hand and only their count is drawn.
 */
async function askSecret(question: string): Promise<string> {
  process.stdout.write(`${cyan("◆")} ${bold(question)}\n  `)
  process.stdin.setRawMode(true)
  process.stdin.resume()
  process.stdin.setEncoding("utf8")

  let value = ""
  await new Promise<void>((resolve) => {
    const handler = (chunk: string) => {
      for (const ch of chunk) {
        if (ch === "\r" || ch === "\n") {
          process.stdin.off("data", handler)
          process.stdin.setRawMode(false)
          process.stdin.pause()
          process.stdout.write("\n")
          resolve()
          return
        }
        if (ch === "\u0003") {
          process.stdin.setRawMode(false)
          process.stdout.write("\n")
          process.exit(130)
        }
        if (ch === "\u007f" || ch === "\b") {
          if (value.length) {
            value = value.slice(0, -1)
            process.stdout.write("\b \b")
          }
          continue
        }
        // Ignore escape sequences (arrows and friends) rather than storing them.
        if (ch < " ") continue
        value += ch
        process.stdout.write("•")
      }
    }
    process.stdin.on("data", handler)
  })
  return value.trim()
}

export async function confirm(question: string, initial = true): Promise<boolean> {
  return select(question, [
    { value: true, label: "Да" },
    { value: false, label: "Нет" },
  ], initial ? 0 : 1)
}

export const ui = { bold, dim, cyan, green }
