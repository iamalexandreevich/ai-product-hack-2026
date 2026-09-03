from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCALHOST = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTGATE_", extra="ignore")

    db_url: str
    token: str | None = None
    bind: str = "127.0.0.1:8400"
    profiles_dir: Path = Path("profiles")
    log_path: Path = Path("logs/decisions.jsonl")
    default_profile: str = "default"

    @property
    def bind_host(self) -> str:
        host, _, _ = self.bind.rpartition(":")
        return host.strip("[]") or self.bind

    @property
    def bind_port(self) -> int:
        _, _, port = self.bind.rpartition(":")
        return int(port)

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
