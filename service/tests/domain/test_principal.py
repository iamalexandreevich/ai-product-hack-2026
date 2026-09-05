import re

import pytest
from ulid import ULID

from agentgate.domain.principal import (
    KEY_ID_PATTERN,
    STATIC_PRINCIPAL,
    ensure_key_id_shape,
    principal_of,
)


def test_the_static_principal_can_never_be_mistaken_for_a_key_id():
    # The whole `principal = coalesce(key_id, 'token')` scheme rests on this
    # one fact; before v3.3 it rested on a docstring.
    assert re.fullmatch(KEY_ID_PATTERN, STATIC_PRINCIPAL) is None


def test_a_freshly_minted_ulid_matches_the_pattern():
    assert re.fullmatch(KEY_ID_PATTERN, str(ULID())) is not None


def test_ensure_key_id_shape_returns_the_id_it_accepted():
    key_id = str(ULID())
    assert ensure_key_id_shape(key_id) == key_id


@pytest.mark.parametrize(
    "bad",
    ["token", "", "01hzkeya" + "0" * 18, "0" * 25, "0" * 27, "0IL" + "0" * 23, "0" * 25 + "!"],
    ids=["static_principal", "empty", "lowercase", "too_short", "too_long",
         "excluded_letters", "punctuation"],
)
def test_ensure_key_id_shape_rejects_anything_but_a_ulid(bad):
    with pytest.raises(ValueError):
        ensure_key_id_shape(bad)


def test_principal_of_still_answers_the_static_token():
    assert principal_of(None) == STATIC_PRINCIPAL
