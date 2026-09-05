"""Who a decision belongs to, as the store and the replay cache name it.

A principal is the issued API key's id, or the literal below for the static
`AGENTGATE_TOKEN`. The SQL of the generated `decisions.principal` column and
the replay cache's key namespace both read this constant, so the two never
drift apart.

The literal is safe as a namespace only because no key id can equal it.
That is not a convention: `KEY_ID_PATTERN` is the shape a key id must have,
`ensure_key_id_shape` refuses to mint anything else, and the database says
the same thing in `ck_api_keys_id_ulid` and `ck_decisions_key_id_ulid`. All
three read the constant below.
"""

import re

STATIC_PRINCIPAL = "token"

# Crockford base32, upper case, 26 characters: the shape of a ULID. Shape,
# not validity -- a timestamp beyond the year 10889 would pass. What is
# needed here is a set that cannot contain STATIC_PRINCIPAL and that reads
# the same in Python and in a Postgres CHECK.
KEY_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"

_KEY_ID_RE = re.compile(KEY_ID_PATTERN)


def principal_of(key_id: str | None) -> str:
    """The issued key's id, or the static token."""
    return key_id or STATIC_PRINCIPAL


def ensure_key_id_shape(key_id: str) -> str:
    """The id a key may be minted with, or ValueError.

    Called where an id is created, not where one is read: inside the service
    an id has already passed this check and the database's own CHECK.
    """
    if _KEY_ID_RE.fullmatch(key_id) is None:
        raise ValueError(f"key id is not a ULID: {key_id!r}")
    return key_id
