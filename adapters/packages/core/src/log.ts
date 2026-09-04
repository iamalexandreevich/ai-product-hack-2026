/** Plain line log. The harness owns stdout, so diagnostics go to a file. */
import fs from "node:fs"
import path from "node:path"

export type Logger = (message: string, detail?: unknown) => void

export function createLogger(logPath: string): Logger {
  let ready = false
  return (message, detail) => {
    try {
      if (!ready) {
        fs.mkdirSync(path.dirname(logPath), { recursive: true })
        ready = true
      }
      const suffix = detail === undefined ? "" : ` ${JSON.stringify(detail)}`
      fs.appendFileSync(logPath, `${new Date().toISOString()} ${message}${suffix}\n`)
    } catch {
      // Logging must never break a tool call.
    }
  }
}

export const nullLogger: Logger = () => {}
