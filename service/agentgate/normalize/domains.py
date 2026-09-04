"""Domain extraction from a command's argv.

Covers the three shapes an agent's shell commands typically embed a
remote host in: a URL (``scheme://host[:port]/...``), an scp-like
``git@host:path`` remote, and a bare ``user@host`` argument to ssh/scp/
rsync/sftp.
"""

import re
from collections.abc import Sequence
from urllib.parse import urlsplit

_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)")
_USER_HOST = re.compile(r"^[\w.-]+@([\w.-]+)$")
_REMOTE_CMDS = {"ssh", "scp", "rsync", "sftp"}


def extract_domains(argv: Sequence[str]) -> list[str]:
    found: list[str] = []
    cmd = argv[0] if argv else ""
    for token in argv:
        host = None
        if "://" in token:
            try:
                host = urlsplit(token).hostname
            except ValueError:
                # e.g. "http://[evil" (unbalanced IPv6-literal bracket)
                # raises ValueError("Invalid IPv6 URL"); a malformed URL
                # yields no domain rather than propagating the exception —
                # this function must never raise on attacker-controlled
                # input (see shell.py's fail-closed wrapping for the
                # complementary rule: any exception AFTER this point
                # still makes the whole action unparseable).
                host = None
        else:
            m = _SCP_LIKE.match(token)
            if m and ("/" in token or "@" in token):
                host = m.group(1)
            elif cmd in _REMOTE_CMDS:
                m2 = _USER_HOST.match(token)
                if m2:
                    host = m2.group(1)
        if host:
            host = host.lower()
            if host not in found:
                found.append(host)
    return found
