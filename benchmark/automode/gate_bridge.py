"""Run the production TypeScript core from a Claude SDK hook, locally or in Docker."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from config import ServiceConfig

ROOT = Path(__file__).resolve().parents[1]
NODE_IMAGE = "node:24-alpine"


def bridge_command(
    runtime: str = "auto", *, offline: bool = False, script: str = "claude_gate_bridge.ts"
) -> list[str]:
    if runtime not in ("auto", "node", "docker"):
        raise ValueError("bridge runtime must be auto, node or docker")
    node = shutil.which("node") if runtime != "docker" else None
    if node:
        return [node, str(ROOT / "tools" / script)]
    if runtime == "node":
        raise ValueError("Node 24 is required for the AgentGate bridge")
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("Install Node 24 or Docker with node:24-alpine for the AgentGate bridge")
    return [
        docker,
        "run",
        "--rm",
        "-i",
        "--pull",
        "never",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *(["--network", "none"] if offline else []),
        "--mount",
        f"type=bind,source={ROOT / 'tools'},target=/repo/benchmark/tools,readonly",
        "--mount",
        f"type=bind,source={ROOT.parent / 'adapters/packages/core/src'},target=/repo/adapters/packages/core/src,readonly",
        NODE_IMAGE,
        "node",
        f"/repo/benchmark/tools/{script}",
    ]


class GateBridge:
    def __init__(self, config: ServiceConfig, *, runtime: str = "auto") -> None:
        self.config = config
        self.runtime = runtime

    def preflight(self) -> None:
        if not (ROOT.parent / "adapters/packages/core/src/request.ts").is_file():
            raise ValueError("AgentGate core is missing; retain the sibling adapters/ checkout")
        command = bridge_command(self.runtime)
        probe = (
            [command[0], "image", "inspect", NODE_IMAGE]
            if "--mount" in command
            else [command[0], "--version"]
        )
        try:
            result = subprocess.run(probe, capture_output=True, text=True, timeout=15, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("AgentGate bridge runtime did not respond") from exc
        if result.returncode:
            raise ValueError(
                "AgentGate bridge runtime unavailable; start Docker and pull node:24-alpine, or install Node 24"
            )
        if "--mount" not in command and int(result.stdout.strip().lstrip("v").split(".")[0]) < 24:
            raise ValueError("AgentGate bridge requires Node 24 or newer")

    async def request(self, **payload) -> dict:
        config = self.config
        data = {
            **payload,
            "config": {
                "url": config.url,
                "token": config.token,
                "profileId": config.profile_id,
                "model": config.model,
                "decideTimeoutMs": int(config.timeout_s * 1000),
                "inspectTimeoutMs": int(config.timeout_s * 1000),
                "onUnavailable": "ask",
            },
        }
        process = await asyncio.create_subprocess_exec(
            *bridge_command(self.runtime),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(data).encode()),
                timeout=config.timeout_s + 20,
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            raise RuntimeError("AgentGate core bridge failed; no decision was measured")
        result = json.loads(stdout)
        if not isinstance(result, dict) or not isinstance(result.get("policy"), dict):
            raise TypeError("Invalid AgentGate core bridge response")
        return result
