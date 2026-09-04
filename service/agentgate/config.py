from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCALHOST = {"127.0.0.1", "localhost", "::1"}


def _split_bind(value: str) -> tuple[str, int]:
    """Parse a validated ``host:port`` bind string.

    Only ever called on a value that already passed ``Settings._validate_bind``,
    so it never raises.
    """
    host, sep, port_str = value.rpartition(":")
    if not sep or not port_str or not host:
        raise ValueError(
            "AGENTGATE_BIND must be in the form host:port "
            f"(bracket IPv6 hosts, e.g. [::1]:8400), got {value!r}"
        )
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(
            f"AGENTGATE_BIND port must be an integer, got {value!r}"
        ) from None
    if not 1 <= port <= 65535:
        raise ValueError(
            f"AGENTGATE_BIND port must be between 1 and 65535, got {value!r}"
        )
    if host.startswith("["):
        if not host.endswith("]") or len(host) <= 2:
            raise ValueError(
                "AGENTGATE_BIND IPv6 host must be bracketed as [host]:port, "
                f"got {value!r}"
            )
        host = host[1:-1]
    elif ":" in host:
        raise ValueError(
            "AGENTGATE_BIND IPv6 host must be bracketed as [host]:port, "
            f"got {value!r}"
        )
    return host, port


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTGATE_", extra="ignore")

    db_url: str
    token: str | None = None
    bind: str = "127.0.0.1:8400"
    profiles_dir: Path = Path("profiles")
    log_path: Path = Path("logs/decisions.jsonl")
    default_profile: str = "default"
    allow_cache_ttl_seconds: int = 86400
    # In-process TTL for the API-key verification cache (agentgate.api.deps).
    # Spec: docs/superpowers/service/specs/api-keys.md, "Проверка на горячем
    # пути" -- kept short (30-60s) because a revoked key stays accepted for
    # up to this long: revocation is not instantaneous, it is bounded by
    # this TTL.
    api_key_cache_ttl_seconds: float = 45.0
    # Full commit SHA the image was built from (Dockerfile ARG GIT_SHA ->
    # ENV AGENTGATE_GIT_SHA). None for a build without the argument.
    git_sha: str | None = None

    @field_validator("git_sha")
    @classmethod
    def _blank_git_sha_is_none(cls, value: str | None) -> str | None:
        # A compose build-arg that was not supplied arrives as "", not unset.
        return value or None

    @field_validator("bind")
    @classmethod
    def _validate_bind(cls, value: str) -> str:
        _split_bind(value)
        return value

    @property
    def bind_host(self) -> str:
        host, _ = _split_bind(self.bind)
        return host

    @property
    def bind_port(self) -> int:
        _, port = _split_bind(self.bind)
        return port

    @property
    def bind_is_localhost(self) -> bool:
        return self.bind_host in _LOCALHOST

    def validate_token_for_bind(self) -> None:
        if not self.bind_is_localhost and not self.token:
            raise ValueError(
                "AGENTGATE_TOKEN is required when AGENTGATE_BIND is not localhost"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
