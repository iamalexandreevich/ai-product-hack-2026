/**
 * The one sanctioned way for the installer to run a harness's own CLI.
 *
 * Codex registers plugins only through `codex plugin …`, so some install steps
 * cannot be file writes. Routing them through an injectable runner keeps the
 * tests hermetic: they assert the exact argv and env instead of shelling out.
 * Failures come back as a value — a harness that refuses a plugin should turn
 * into a warning, never an exception out of `install()`.
 */
import { execFileSync } from "node:child_process"

export type RunResult = { ok: boolean; code: number; stdout: string; stderr: string }

export type Runner = (
  cmd: string,
  args: string[],
  opts?: { env?: NodeJS.ProcessEnv; cwd?: string; timeoutMs?: number },
) => RunResult

export const execRunner: Runner = (cmd, args, opts = {}) => {
  try {
    const stdout = execFileSync(cmd, args, {
      encoding: "utf8",
      env: opts.env ?? process.env,
      cwd: opts.cwd,
      timeout: opts.timeoutMs ?? 120_000,
      stdio: ["ignore", "pipe", "pipe"],
    })
    return { ok: true, code: 0, stdout, stderr: "" }
  } catch (error) {
    const e = error as { status?: number; stdout?: string; stderr?: string; message?: string }
    return {
      ok: false,
      code: typeof e.status === "number" ? e.status : 1,
      stdout: e.stdout ?? "",
      stderr: e.stderr ?? e.message ?? "",
    }
  }
}
