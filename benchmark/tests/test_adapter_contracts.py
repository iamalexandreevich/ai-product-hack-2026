"""Execute real TypeScript builders with local Node or a cached Docker image."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def test_production_adapter_requests_match_service():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node:
        command = [node, str(root / "tools/check_adapters.ts"), "--json"]
    else:
        docker = shutil.which("docker")
        if not docker:
            pytest.skip("Install Node 24 or Docker with the node:24-alpine image")
        image = "node:24-alpine"
        try:
            available = subprocess.run(
                [docker, "image", "inspect", image],
                capture_output=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("Docker did not respond; start Docker Desktop and retry")
        if available.returncode:
            pytest.skip("Docker image unavailable or inaccessible; run: docker pull node:24-alpine")
        command = [
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--mount",
            f"type=bind,source={root.parent},target=/repo,readonly",
            image,
            "node",
            "/repo/benchmark/tools/check_adapters.ts",
            "--json",
        ]
    output = subprocess.check_output(command, text=True, encoding="utf-8", timeout=60)
    for kind, body in json.loads(output).items():
        kind = kind.split("_", 1)[0]
        schema = json.loads(
            (root.parent / f"contracts/{kind}_request.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(body)
