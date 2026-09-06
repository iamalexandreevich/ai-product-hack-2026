"""Exercise real pinned ActBench file tools through our guard, without a model or server."""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(source):
    from baselines.actbench import verify_source
    from baselines.actbench_gateway import GuardedTools

    verify_source(source)
    sys.path.insert(0, str(source / "scripts"))
    import lib_mcp_gateway as gateway

    (ROOT / "results").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "results") as temporary:
        workspace = Path(temporary) / "task_B9_T01" / "workspace"
        workspace.mkdir(parents=True)
        gateway.register_context(context_id="integration-test", workspace=workspace)
        events = []
        requests = []
        decision = "deny"

        def post(kind, body):
            requests.append(body)
            if kind == "decide":
                return {"decision": decision, "reason": "test policy"}
            return {"verdict": "mask", "output": "authoritative replacement"}

        guard = GuardedTools(gateway, source / "tasks", post, events.append)
        params = {
            "name": "actbench_write_file",
            "arguments": {
                "context_id": "integration-test",
                "path": "probe.txt",
                "content": "probe content",
            },
        }
        result = guard.dispatch(params)
        assert result["isError"] and not (workspace / "probe.txt").exists()
        decision = "ask"
        result = guard.dispatch(params)
        assert result["isError"] and not (workspace / "probe.txt").exists()
        decision = "allow"
        result = guard.dispatch(params)
        assert (workspace / "probe.txt").read_text() == "probe content"
        assert result["content"] == [{"type": "text", "text": "authoritative replacement"}]
        assert gateway.get_context_traces("integration-test")[-1]["result"] == result
        assert requests[-2]["call_id"] == requests[-1]["call_id"]
        gateway.unregister_context("integration-test")
    print(
        json.dumps(
            {
                "native_tool_cases": 3,
                "blocked_side_effect_checks": 2,
                "authoritative_mask_checks": 1,
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "results/vendor/actbench")
