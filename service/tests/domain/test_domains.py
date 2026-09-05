import pytest

from agentgate.domain.domains import domain_allowed


@pytest.mark.parametrize(
    ("domain", "allowed", "expected"),
    [
        ("pypi.org", ["pypi.org"], True),
        ("files.pypi.org", ["pypi.org"], True),
        ("PyPI.org", ["pypi.org"], True),
        ("pypi.org", ["PyPI.org"], True),
        ("evil.sh", ["pypi.org"], False),
        ("notpypi.org", ["pypi.org"], False),
        ("pypi.org.evil.sh", ["pypi.org"], False),
        ("pypi.org", [], False),
    ],
    ids=[
        "exact", "subdomain", "action_case_folds", "allowlist_case_folds",
        "other_domain", "suffix_is_not_a_subdomain", "domain_as_a_prefix", "empty_allowlist",
    ],
)
def test_domain_allowed(domain, allowed, expected):
    assert domain_allowed(domain, allowed) is expected
