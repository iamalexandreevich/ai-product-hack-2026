import statistics
import time
from pathlib import Path

import pytest

from agentgate.api.schemas import InspectRequest
from agentgate.inspect.detectors import Action
from agentgate.inspect.mask import PRIVATE_KEY_REPLACEMENT, SECRET_REPLACEMENT
from agentgate.inspect.secrets import NEEDLE_FORMS, entropy_candidates_allowed, redact_line, scan_secrets
from tests.factories import WORKSPACE, inspect_request

FIXTURES = Path(__file__).parent / "fixtures"


def _scan(text: str, candidates: bool = True):
    return scan_secrets(text, entropy_candidates=candidates)


FORMS = [
    ("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", f"AWS_ACCESS_KEY_ID={SECRET_REPLACEMENT}"),
    ("temporary: ASIAIOSFODNN7EXAMPLE", f"temporary: {SECRET_REPLACEMENT}"),
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", f"aws_secret_access_key = {SECRET_REPLACEMENT}"),
    ("remote: https://ghp_16C7e42F292c6912E7710c838347Ae178B4a@github.com", f"remote: https://{SECRET_REPLACEMENT}@github.com"),
    ("token: github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF", f"token: {SECRET_REPLACEMENT}"),
    ("PRIVATE-TOKEN: glpat-AbCdEfGhIjKlMnOpQrSt", f"PRIVATE-TOKEN: {SECRET_REPLACEMENT}"),
    ("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH", f"OPENAI_API_KEY={SECRET_REPLACEMENT}"),
    ("key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH", f"key {SECRET_REPLACEMENT}"),
    ("SLACK_BOT_TOKEN=xoxb-1234567890-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", f"SLACK_BOT_TOKEN={SECRET_REPLACEMENT}"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", f"Authorization: Bearer {SECRET_REPLACEMENT}"),
    ("cookie=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", f"cookie={SECRET_REPLACEMENT}"),
    ("> x-api-key: 9f8e7d6c5b4a39281706f5e4d3c2b1a0", f"> x-api-key: {SECRET_REPLACEMENT}"),
    ("Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==", f"Proxy-Authorization: Basic {SECRET_REPLACEMENT}"),
    ("DATABASE_PASSWORD=correct-horse-battery", f"DATABASE_PASSWORD={SECRET_REPLACEMENT}"),
    ('  "api_key": "abcdefghijklmnop",', f'  "api_key": "{SECRET_REPLACEMENT}",'),
    ("credential: hunter2hunter2", f"credential: {SECRET_REPLACEMENT}"),
    ("X-Api-Key\t: 9f8e7d6c5b4a39281706f5e4d3c2b1a0", f"X-Api-Key\t: {SECRET_REPLACEMENT}"),
    ("Authorization : Bearer abcdefghijklmn", f"Authorization : Bearer {SECRET_REPLACEMENT}"),
]
FORM_IDS = [
    "aws_akia", "aws_asia", "aws_secret", "github_ghp", "github_pat", "gitlab", "openai", "anthropic", "slack",
    "jwt_bearer", "jwt_bare", "x_api_key", "proxy_basic", "name_password", "name_json_api_key", "name_credential",
    "x_api_key_spaced_colon", "authorization_spaced_colon",
]


@pytest.mark.parametrize(("line", "expected"), FORMS, ids=FORM_IDS)
def test_recognized_forms_are_redacted_by_value_and_keep_the_key(line, expected):
    findings = _scan(f"before\n{line}\nafter\n", candidates=False)
    assert [f.line for f in findings] == [1]
    assert findings[0].action is Action.redact
    assert findings[0].rule_id == "inspect.secret"
    assert findings[0].candidate_key is None
    assert findings[0].rewritten == expected


@pytest.mark.parametrize(("line", "_expected"), FORMS, ids=FORM_IDS)
def test_a_needle_prefilter_admits_every_line_its_own_pattern_matches(line, _expected):
    """The prefilter may only skip a line the pattern would certainly miss."""
    for form in NEEDLE_FORMS:
        if form.pattern.search(line):
            assert any(needle in line.lower() for needle in form.needles), (form.needles, line)


def test_redact_line_hides_a_recognized_form_and_keeps_the_rest():
    line = 'curl -H "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123456789" https://api.example.com'
    redacted = redact_line(line)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert SECRET_REPLACEMENT in redacted
    assert redacted.startswith("curl -H ")
    assert redacted.endswith(" https://api.example.com")


def test_redact_line_returns_a_plain_line_unchanged():
    assert redact_line("git status --short") == "git status --short"


def test_redact_line_leaves_an_entropy_candidate_alone():
    line = "DATABASE_URL=postgres://app:s3cr3tP4ssw0rd@db.internal:5432/app"
    assert redact_line(line) == line


def test_a_jwt_whose_header_is_not_json_is_not_a_secret():
    assert _scan("x=eyJub3QuanNvbg.eyJzdWIiOiIxIn0.abcdefghijklmnop\n", candidates=False) == []


def test_a_pem_block_is_one_range_redacted_to_one_marker():
    text = "cat key.pem\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nMIIEow\n-----END RSA PRIVATE KEY-----\ndone\n"
    findings = _scan(text, candidates=False)
    assert len(findings) == 1
    assert (findings[0].line, findings[0].last) == (1, 4)
    assert findings[0].rewritten == PRIVATE_KEY_REPLACEMENT


def test_an_unterminated_pem_block_is_redacted_to_the_end():
    findings = _scan("-----BEGIN PRIVATE KEY-----\nMIIE\nMIIE\n", candidates=False)
    assert (findings[0].line, findings[0].last) == (0, 3)


def test_an_entropy_candidate_carries_its_key_and_is_a_candidate():
    findings = _scan("DATABASE_URL=postgres://app:s3cr3tP4ssw0rd@db.internal:5432/app\n", candidates=True)
    assert findings[0].candidate_key == "DATABASE_URL"
    assert findings[0].rewritten == f"DATABASE_URL={SECRET_REPLACEMENT}"


def test_entropy_candidates_are_skipped_when_disabled():
    assert _scan("DATABASE_URL=postgres://app:s3cr3tP4ssw0rd@db.internal:5432/app\n", candidates=False) == []


def test_recognized_forms_are_final_even_when_candidates_are_disabled():
    findings = _scan("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n", candidates=False)
    assert findings and findings[0].candidate_key is None


def test_a_short_or_low_entropy_value_is_not_a_candidate():
    assert _scan("NAME=alexander\nGREETING=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", candidates=True) == []


def test_two_secrets_on_one_line_are_both_redacted_in_one_finding():
    findings = _scan("AKIAIOSFODNN7EXAMPLE and ghp_16C7e42F292c6912E7710c838347Ae178B4a\n", candidates=False)
    assert len(findings) == 1
    assert findings[0].rewritten == f"{SECRET_REPLACEMENT} and {SECRET_REPLACEMENT}"


def test_the_false_positive_corpus_is_never_redacted():
    text = (FIXTURES / "secret_false_positives.txt").read_text(encoding="utf-8")
    lines = text.split("\n")
    findings = scan_secrets(text, entropy_candidates=True)
    assert findings == [], [lines[f.line] for f in findings]


def test_a_secret_on_the_last_line_without_a_trailing_newline_is_found():
    findings = _scan("ok\nAWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", candidates=False)
    assert [f.line for f in findings] == [1]


@pytest.mark.parametrize(
    ("provenance", "cwd", "expected"),
    [
        ({"kind": "file", "path": f"{WORKSPACE}/.env"}, WORKSPACE, True),
        ({"kind": "file", "path": "/home/u/.aws/credentials"}, WORKSPACE, True),
        ({"kind": "file", "path": f"{WORKSPACE}/src/main.py"}, WORKSPACE, False),
        ({"kind": "file", "path": f"{WORKSPACE}/package-lock.json"}, WORKSPACE, False),
        ({"kind": "shell", "command": "printenv"}, WORKSPACE, True),
        ({"kind": "shell", "command": "env | sort"}, WORKSPACE, True),
        ({"kind": "shell", "command": "cat .env"}, WORKSPACE, True),
        ({"kind": "shell", "command": "cat README.md"}, WORKSPACE, False),
        ({"kind": "shell", "command": "git status"}, WORKSPACE, False),
        ({"kind": "web", "url": "https://example.com"}, WORKSPACE, True),
        ({"kind": "mcp", "server": "s", "tool": "t"}, WORKSPACE, True),
        ({"kind": "subagent", "session_id": "x"}, WORKSPACE, False),
        ({"kind": "unknown"}, WORKSPACE, False),
    ],
    ids=[
        "file_dotenv", "file_aws_credentials", "file_source", "file_lockfile", "shell_printenv", "shell_env_pipe",
        "shell_cat_dotenv", "shell_cat_readme", "shell_git_status", "web", "mcp", "subagent", "unknown",
    ],
)
def test_entropy_candidates_follow_provenance(provenance, cwd, expected):
    request: InspectRequest = inspect_request(provenance=provenance, args={"cwd": cwd})
    assert entropy_candidates_allowed(request.provenance, cwd) is expected


def _p50_ms(text: str) -> float:
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        scan_secrets(text, entropy_candidates=True)
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def _many_lines(make_line) -> str:
    lines, total, i = [], 0, 0
    while total < 262_144:
        line = make_line(i)
        lines.append(line)
        total += len(line.encode()) + 1
        i += 1
    return "\n".join(lines)


def _one_line(unit: str) -> str:
    return unit * (262_144 // len(unit.encode()) + 1)


# The budget bounds the scan, not the reporting: a finding costs about
# 5 us to rewrite and record, so an output whose every line is a secret
# needs some 45 ms for 256 KB no matter how the scan is arranged. The
# dense corpora below are dense in *hints*, and one corpus carries real
# secrets at a rate a log realistically reaches.
@pytest.mark.parametrize(
    "corpus_factory",
    [
        lambda: _many_lines(lambda i: f"[INFO] step {i}: build succeeded in {i % 7}.{i % 100}s see README.md section {i % 50} for setup notes"),
        lambda: _many_lines(lambda i: f"KEY_{i}="),
        lambda: _many_lines(lambda i: f"token_{i}=abc{i}"),
        lambda: _many_lines(lambda i: f"AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI{i}K7MDENGbPxRfiCY" if i % 50 == 0 else f"[INFO] step {i} build ok"),
        lambda: _one_line("-----BEGIN "),
        lambda: _one_line("token=a "),
        lambda: _one_line("eyJ."),
        lambda: "\n" * 262_144,
    ],
    ids=[
        "ordinary_log", "key_equals_dense", "token_hint_dense_lines", "secret_lines_one_in_fifty",
        "begin_without_end_single_line", "token_hint_dense_single_line", "jwt_hint_dense_single_line",
        "many_empty_lines",
    ],
)
def test_scan_secrets_p50_under_5ms_for_256kb(corpus_factory):
    p50 = _p50_ms(corpus_factory())
    assert p50 <= 5.0, f"p50={p50:.3f}ms"
