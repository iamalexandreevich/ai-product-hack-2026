"""Who a decision belongs to, as the store and the replay cache name it.

A principal is the issued API key's id, or the literal below for the static
`AGENTGATE_TOKEN`. The SQL of the generated `decisions.principal` column and
the replay cache's key namespace both read this constant, so the two never
drift apart.
"""

STATIC_PRINCIPAL = "token"


def principal_of(key_id: str | None) -> str:
    """The issued key's id, or the static token.

    A key id is a ULID -- 26 characters of uppercase Crockford base32,
    written only by the CLI -- so the literal can never collide with one
    and never contains the `:` the replay key uses as a separator.
    """
    return key_id or STATIC_PRINCIPAL
