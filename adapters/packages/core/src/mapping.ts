/**
 * Harness tool calls -> the service's five-value `tool` enum.
 *
 * The enum cannot express what the harnesses actually do, so every mapping here
 * is lossy on purpose and the harness-native name is preserved separately in
 * `tool_name` (see metadata in request.ts). The lossy cases are written up in
 * docs/contract-gaps.md; when the service grows a `tool_name` field this file
 * stops throwing information away.
 *
 * Argument shapes below were read off a live opencode 1.17.18 session, not
 * guessed: read -> {filePath}, bash -> {command, description},
 * webfetch -> {url, format}.
 */
import path from "node:path"
import type { ActionArgs, Provenance, Tool } from "./protocol.ts"

export type MappedAction = {
  tool: Tool
  /** Harness-native name, e.g. "bash", "task", "github_create_issue". */
  toolName: string
  raw: string
  args: ActionArgs
  provenance: Provenance
}

const FILE_READ_TOOLS = new Set(["read", "glob", "grep", "list", "ls"])
const FILE_WRITE_TOOLS = new Set(["write", "edit", "patch", "multiedit"])

function str(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function absolute(cwd: string, candidate: string): string {
  if (!candidate) return ""
  return path.isAbsolute(candidate) ? candidate : path.resolve(cwd, candidate)
}

function hostOf(url: string): string[] {
  try {
    return [new URL(url).hostname]
  } catch {
    return []
  }
}

/** Best-effort split of opencode's `<server>_<tool>` MCP naming. */
function splitMcpName(name: string): { server: string; tool: string } {
  const at = name.indexOf("_")
  if (at <= 0) return { server: "mcp", tool: name }
  return { server: name.slice(0, at), tool: name.slice(at + 1) }
}

export function mapToolCall(
  toolName: string,
  args: Record<string, unknown>,
  cwd: string,
): MappedAction {
  const base = { toolName, args: { cwd } as ActionArgs }

  if (toolName === "bash" || toolName === "shell" || toolName === "execute") {
    const command = str(args.command)
    return { ...base, tool: "shell", raw: command, provenance: { kind: "shell", command } }
  }

  if (FILE_READ_TOOLS.has(toolName)) {
    const target = absolute(cwd, str(args.filePath) || str(args.path) || str(args.pattern))
    return {
      ...base,
      tool: "file_read",
      raw: "",
      args: { cwd, paths: target ? [target] : [] },
      provenance: { kind: "file", path: target },
    }
  }

  if (FILE_WRITE_TOOLS.has(toolName)) {
    const target = absolute(cwd, str(args.filePath) || str(args.path))
    return {
      ...base,
      tool: "file_write",
      raw: str(args.content) || str(args.newString),
      args: { cwd, paths: target ? [target] : [] },
      provenance: { kind: "file", path: target },
    }
  }

  if (toolName === "webfetch") {
    const url = str(args.url)
    return {
      ...base,
      tool: "network",
      raw: url,
      args: { cwd, domains: hostOf(url) },
      provenance: { kind: "web", url },
    }
  }

  if (toolName === "websearch") {
    const query = str(args.query)
    return { ...base, tool: "network", raw: query, provenance: { kind: "web", url: query } }
  }

  if (toolName === "task") {
    // A subagent is neither shell nor file nor network. `mcp_call` is the least
    // wrong slot in the current enum; the prompt is the part worth classifying.
    const prompt = str(args.prompt)
    return {
      ...base,
      tool: "mcp_call",
      raw: prompt,
      args: {
        cwd,
        mcp: {
          server: "subagent",
          tool: str(args.subagent_type) || "task",
          arguments: { description: str(args.description), prompt },
        },
      },
      provenance: { kind: "subagent", session_id: "" },
    }
  }

  const { server, tool } = splitMcpName(toolName)
  return {
    ...base,
    tool: "mcp_call",
    raw: JSON.stringify(args ?? {}),
    args: { cwd, mcp: { server, tool, arguments: args ?? {} } },
    provenance: { kind: "mcp", server, tool },
  }
}

/**
 * Permission requests seen on the patched build, which include non-tool kinds
 * the fallback path never sees at all. `external_directory` was the single most
 * common request in a live session, and it carries either a file or a command.
 */
export function mapPermission(
  permission: string,
  metadata: Record<string, unknown>,
  cwd: string,
): MappedAction {
  const command = str(metadata.command)

  if (permission === "bash" || permission === "doom_loop" || command) {
    return {
      toolName: permission,
      tool: "shell",
      raw: command,
      args: { cwd },
      provenance: { kind: "shell", command },
    }
  }

  if (permission === "edit" || permission === "write") {
    const target = absolute(cwd, str(metadata.filePath) || str(metadata.filepath))
    return {
      toolName: permission,
      tool: "file_write",
      raw: "",
      args: { cwd, paths: target ? [target] : [] },
      provenance: { kind: "file", path: target },
    }
  }

  if (permission === "webfetch") {
    const url = str(metadata.url)
    return {
      toolName: permission,
      tool: "network",
      raw: url,
      args: { cwd, domains: hostOf(url) },
      provenance: { kind: "web", url },
    }
  }

  if (permission === "read" || permission === "external_directory") {
    const dirs = Array.isArray(metadata.directories) ? metadata.directories.map(str) : []
    const target = absolute(cwd, str(metadata.filepath) || str(metadata.filePath) || dirs[0] || "")
    return {
      toolName: permission,
      tool: "file_read",
      raw: "",
      args: { cwd, paths: target ? [target] : [] },
      provenance: { kind: "file", path: target },
    }
  }

  return mapToolCall(permission, metadata, cwd)
}
