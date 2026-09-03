from agentgate.api.schemas import DecideRequest
from agentgate.normalize import normalize
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import Profile
from agentgate.stage2.prompt import build_system_prompt, build_user_message

WS = "/home/u/repo"
P = with_workspace(Profile.model_validate({
    "id": "t", "allowed_paths": ["${WORKSPACE}"], "protected_paths": [".env*", ".git/hooks/**"],
    "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
    "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "q"}}},
    "prose": {"environment": "TS monorepo", "allow": "pnpm ok", "soft_deny": "no infra/"},
}), WS)


def test_system_prompt_contains_profile_and_prose():
    s = build_system_prompt(P)
    assert "workspace=/home/u/repo" in s
    assert "network=allowlist(pypi.org)" in s
    assert "protected=.env*,.git/hooks/**" in s
    assert "TS monorepo" in s and "pnpm ok" in s and "no infra/" in s
    assert '"decision"' in s  # response schema described


def test_user_message_layout_and_blindness():
    a = normalize(DecideRequest(harness="t", tool="shell", raw="npm install lodahs && rm -rf ./dist",
                                args={"cwd": WS}, user_request="x", metadata={"secret": "LEAK"}))
    m = build_user_message(a, "почини сборку", "passed: no hard-deny match, not in allowlist")
    assert m.startswith("[TASK] почини сборку\n")
    assert "[ACTION] tool=shell cwd=/home/u/repo" in m
    assert 'argv=[["npm","install","lodahs"],["rm","-rf","./dist"]]' in m
    assert "paths=[/home/u/repo/dist]" in m
    assert "[FLAGS] unparseable=false has_eval=false has_subst=false" in m
    assert m.rstrip().endswith("[STAGE1] passed: no hard-deny match, not in allowlist")
    assert "LEAK" not in m


def test_system_prompt_is_stable_across_actions():
    assert build_system_prompt(P) == build_system_prompt(P)
