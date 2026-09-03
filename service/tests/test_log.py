import json

from agentgate.log.jsonl import JsonlLogger


def test_jsonl_appends_lines(tmp_path):
    path = tmp_path / "logs" / "d.jsonl"
    logger = JsonlLogger(path)
    logger.write({"a": 1, "текст": "да"})
    logger.write({"b": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"a": 1, "текст": "да"}
    assert json.loads(lines[1]) == {"b": 2}


def test_jsonl_write_error_does_not_raise(tmp_path):
    bad = tmp_path / "file"
    bad.write_text("x")
    JsonlLogger(bad / "cannot" / "create.jsonl").write({"a": 1})
