"""`python -m agentgate keys ...` -- CLI for minting, listing and revoking API keys.

Design: docs/superpowers/service/specs/api-keys.md, "Генерация -- из CLI, не
через эндпоинт": an HTTP endpoint for issuing keys would need its own
authentication -- a key to issue keys, begging the question. That bootstrap
problem is solved either by a master key (same problem one level up) or by
access to the machine; access to the machine already exists, so the CLI is
the entrypoint, dispatched from `agentgate.__main__.main` before the server
starts (a `keys` first argument never reaches `uvicorn.run`).

Discipline enforced throughout this module:
- `create` prints the plaintext key to stdout and nowhere else -- never a
  log line, never back into the database (`ApiKeyRepo.create` stores only
  the SHA-256 hash).
- `list` and `revoke` only ever handle `key_id`; neither the key nor its
  hash is retrievable after `create` returns.
- Any failure (bad arguments, unknown key_id, a store error) goes to
  stderr with a non-zero exit code; stdout carries nothing on failure.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from agentgate.config import Settings, get_settings
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo

_DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


class DurationError(ValueError):
    pass


def _parse_duration(value: str) -> timedelta:
    """Parse a duration like "90d", "12h", "30m", "45s" into a `timedelta`."""
    if len(value) < 2 or value[-1] not in _DURATION_UNITS:
        raise DurationError(
            f"invalid duration {value!r}: expected a positive integer followed by one of d/h/m/s (e.g. 90d)"
        )
    amount_str, unit = value[:-1], value[-1]
    try:
        amount = int(amount_str)
    except ValueError:
        raise DurationError(f"invalid duration {value!r}: {amount_str!r} is not an integer") from None
    if amount <= 0:
        raise DurationError(f"invalid duration {value!r}: amount must be positive")
    return timedelta(**{_DURATION_UNITS[unit]: amount})


def _build_repo(settings: Settings):
    engine = make_engine(settings.db_url)
    sf = make_session_factory(engine)
    return ApiKeyRepo(sf), engine


async def _create(settings: Settings, label: str, expires: str | None) -> int:
    expires_at = None
    if expires:
        try:
            expires_at = datetime.now(timezone.utc) + _parse_duration(expires)
        except DurationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    repo, engine = _build_repo(settings)
    try:
        plaintext, _record = await repo.create(label=label, expires_at=expires_at)
    finally:
        await engine.dispose()
    print(plaintext)  # the ONLY place the plaintext key is ever written
    return 0


_LIST_COLUMNS = ("id", "label", "created_at", "expires_at", "revoked_at", "last_used_at")


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _list(settings: Settings) -> int:
    repo, engine = _build_repo(settings)
    try:
        records = await repo.list()
    finally:
        await engine.dispose()
    widths = {"id": 26, "label": 24, "created_at": 25, "expires_at": 25, "revoked_at": 25, "last_used_at": 25}
    header = "  ".join(col.ljust(widths[col]) for col in _LIST_COLUMNS)
    print(header)
    for r in records:
        row = "  ".join(_fmt(getattr(r, col)).ljust(widths[col]) for col in _LIST_COLUMNS)
        print(row)
    return 0


async def _revoke(settings: Settings, key_id: str) -> int:
    repo, engine = _build_repo(settings)
    try:
        found = await repo.revoke(key_id)
    finally:
        await engine.dispose()
    if not found:
        print(f"error: no such key: {key_id}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agentgate keys")
    sub = parser.add_subparsers(dest="action", required=True)

    p_create = sub.add_parser("create", help="mint a new API key")
    p_create.add_argument("--label", required=True, help="human-readable label: whose key this is")
    p_create.add_argument("--expires", default=None, help="duration until expiry, e.g. 90d, 12h, 30m (default: never)")

    sub.add_parser("list", help="list all keys (never shows the key or its hash)")

    p_revoke = sub.add_parser("revoke", help="soft-revoke a key by id")
    p_revoke.add_argument("key_id")

    return parser


def run_keys_cli(argv: list[str], settings: Settings | None = None) -> int:
    """Entrypoint for `python -m agentgate keys ...`. Returns a process exit code.

    `settings` defaults to `get_settings()` (the real entrypoint's
    behavior); tests pass an explicit `Settings` instead, both to avoid
    spawning a process and to sidestep `get_settings()`'s process-global
    `lru_cache`.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed its own usage/error to stderr and wants
        # to exit the process; return that code instead so callers (and
        # tests) can convert it into a normal function return.
        return exc.code if isinstance(exc.code, int) else 2

    settings = settings or get_settings()

    try:
        if args.action == "create":
            return asyncio.run(_create(settings, args.label, args.expires))
        if args.action == "list":
            return asyncio.run(_list(settings))
        if args.action == "revoke":
            return asyncio.run(_revoke(settings, args.key_id))
    except Exception as exc:  # noqa: BLE001 - CLI errors go to stderr with a non-zero exit, never a stack trace on stdout
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2  # unreachable: argparse's `required=True` on the subparsers rejects anything else
