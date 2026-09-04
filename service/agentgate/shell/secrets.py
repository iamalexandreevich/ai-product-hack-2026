"""What counts as a secret file. One list, service-wide.

Two lists were two chances to add a pattern to one and forget the other,
and a missed pattern here is a hole in exfil detection.
"""

from agentgate.normalize.paths import matches_any

SECRET_PATTERNS: tuple[str, ...] = (
    ".env*", "*.pem", "id_rsa*", "id_ed25519*", "*.key", "*.p12",
    "credentials", ".netrc", ".npmrc", ".git-credentials",
    "~/.ssh/**", "~/.aws/**", "~/.kube/**",
)


def is_secret_path(path: str, workspace: str | None) -> bool:
    """True if ``path`` names a secret file. A slash-free argv token is
    matched by basename, so this also answers "is this bare word the name
    of a secret" for a caller holding a token rather than a path.
    """
    return matches_any(path, SECRET_PATTERNS, workspace)
