/**
 * How OPENMAGI looks in a terminal.
 *
 * One module for the palette, the wordmark and the small marks the installer
 * prints, so a colour is chosen once rather than spelled out at each call site.
 * The palette is the product's own (see the design notes): a purple brand, a
 * lime signal, and three verdict colours that match what the guard answers.
 *
 * Everything degrades to plain text. A pipe, a dumb terminal, `NO_COLOR` or
 * `TERM=dumb` all mean the same thing here -- escape codes would end up in
 * someone's log file, and an install log full of `\x1b[38;5;154m` is worse than
 * an install log with no colour at all.
 */
const ESC = "\x1b"

/**
 * 256-colour indices rather than truecolour: the installer runs inside whatever
 * terminal the user already had open, and 256 colours is the floor that every
 * one of them clears. The hex values these approximate live in the design
 * notes; the index is what actually reaches the screen.
 */
const CODE = {
  brand: 98, // #7B4FD6 — purple, the brand
  lime: 154, // #B6FF2E — signal, section headings, "online"
  orange: 202, // #FF6A00 — system labels and the `→` of a next command
  deny: 197, // #FF2E4A
  ask: 220, // #FFC93A
  grey: 60, // #7D7391 — captions and inactive labels
} as const

export type Ink = keyof typeof CODE

/**
 * Colour is a property of the stream, not of the process: `openmagi install`
 * piped into `tee` must stay readable, while the same command in a terminal
 * gets the full palette. Checked once at load, because a stream does not change
 * its mind halfway through an install.
 */
export const COLOUR =
  Boolean(process.stdout.isTTY) &&
  !process.env.NO_COLOR &&
  process.env.TERM !== "dumb"

export function paint(ink: Ink, text: string): string {
  return COLOUR ? `${ESC}[38;5;${CODE[ink]}m${text}${ESC}[0m` : text
}

/** A filled plaque: dark text on a colour, for a verdict or a status. */
export function plaque(ink: Ink, text: string): string {
  return COLOUR ? `${ESC}[48;5;${CODE[ink]}m${ESC}[38;5;232m${text}${ESC}[0m` : text
}

/**
 * The wordmark, kept as two blocks rather than one.
 *
 * Splitting it here is what makes the two halves colourable at all: joined into
 * single lines, `OPEN` and `MAGI` could only be separated by counting columns,
 * and a column count silently rots the first time a glyph changes width.
 *
 * The face is ANSI Shadow: a solid body in `█` with a raised edge drawn in box
 * characters. That edge is why the two-tone treatment below exists -- painted
 * flat, the letters lose the depth the face is built around.
 */
const OPEN = [
  " ██████╗ ██████╗ ███████╗███╗   ██╗",
  "██╔═══██╗██╔══██╗██╔════╝████╗  ██║",
  "██║   ██║██████╔╝█████╗  ██╔██╗ ██║",
  "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║",
  "╚██████╔╝██║     ███████╗██║ ╚████║",
  " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝",
]

const MAGI = [
  "███╗   ███╗ █████╗  ██████╗ ██╗",
  "████╗ ████║██╔══██╗██╔════╝ ██║",
  "██╔████╔██║███████║██║  ███╗██║",
  "██║╚██╔╝██║██╔══██║██║   ██║██║",
  "██║ ╚═╝ ██║██║  ██║╚██████╔╝██║",
  "╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝",
]

/** The body of a glyph against its raised edge. */
const BODY = "█"

/**
 * Dimmer companions, same hue. The edge sits behind the body, so giving it the
 * body's colour flattens the face into a silhouette.
 */
const SHADE: Record<string, number> = { lime: 100, brand: 61 }

/** Paints one line, switching between body and edge as the characters change. */
function twoTone(line: string, ink: Ink): string {
  if (!COLOUR) return line
  let out = ""
  let current: "body" | "edge" | null = null
  for (const ch of line) {
    const want = ch === BODY ? "body" : ch === " " ? current : "edge"
    if (want !== current && want !== null) {
      out += want === "body" ? `${ESC}[38;5;${CODE[ink]}m` : `${ESC}[38;5;${SHADE[ink]}m`
      current = want
    }
    out += ch
  }
  return out + `${ESC}[0m`
}

/**
 * `OPEN` in lime, `MAGI` in brand purple.
 *
 * Without colour the halves would run together into one word, so the plain
 * fallback is the name in text rather than a wordmark nobody can read.
 */
export function wordmark(): string {
  if (!COLOUR) return "OPENMAGI"
  return OPEN.map((line, i) => `${twoTone(line, "lime")} ${twoTone(MAGI[i], "brand")}`).join("\n")
}

export const mark = {
  ok: () => paint("lime", "✔"),
  next: () => paint("orange", "→"),
  warn: () => paint("ask", "!"),
  bad: () => paint("deny", "✗"),
  online: () => paint("lime", "■ ONLINE"),
  standby: () => paint("grey", "□ STANDBY"),
}
