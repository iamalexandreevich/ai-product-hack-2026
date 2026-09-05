"""The one domain check the allowlist rule and the trusted-allow rule share.

A domain is allowed if it exactly matches an allowlisted domain or is a
subdomain of one. Comparison folds case; a domain that merely has an
allowlisted domain as a string prefix (`pypi.org.evil.sh`) is not a match.
"""


def domain_allowed(domain: str, allowed: list[str]) -> bool:
    domain = domain.lower()
    for candidate in allowed:
        candidate = candidate.lower()
        if domain == candidate or domain.endswith("." + candidate):
            return True
    return False
