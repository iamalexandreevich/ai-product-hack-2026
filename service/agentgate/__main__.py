"""Process entrypoint: `uv run python -m agentgate`.

Argument parsing and `uvicorn.run`, nothing else -- the wiring lives in
agentgate.bootstrap, which is where a test exercises it without booting a
real server (`uvicorn.run` blocks the calling thread running its own event
loop and cannot be driven from inside a unit test).
"""

import asyncio
import logging
import sys

import uvicorn

from agentgate.bootstrap import build_service
from agentgate.config import get_settings


def main(argv: list[str] | None = None) -> None:
    """Process entrypoint.

    ``python -m agentgate keys ...`` dispatches to the key-management CLI
    (agentgate.cli) instead of starting the server -- see that module's
    docstring for why key issuance is a CLI concern, not an HTTP endpoint.
    Any other (or no) argument starts the server as before.
    """
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "keys":
        from agentgate.cli import run_keys_cli

        sys.exit(run_keys_cli(argv[1:]))

    logging.basicConfig(level=logging.INFO)
    service = asyncio.run(build_service(get_settings()))
    uvicorn.run(service.app, host=service.settings.bind_host, port=service.settings.bind_port)


if __name__ == "__main__":
    main()
