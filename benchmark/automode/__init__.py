"""Automode implementations under test.

The benchmark framework talks to exactly one abstraction — ``AutomodeAdapter`` — so the
dataset, the scorer, the metrics, the storage and the reports are shared by every
implementation that is ever measured. ``ServerAutomodeAdapter`` (our AgentGate service
over HTTP) is the only implementation today and the default.

A second implementation, ``ClaudeCodeAutomodeAdapter``, poses the same pair to Claude
Code's native auto-mode classifier through the Claude Agent SDK. It is not the default
and needs a disposable sandbox to run; see ``automode/claude_code.py``.

Not to be confused with the repository's top-level ``adapters/``: that is direction 1,
the harness plugins that call our service in production. Nothing from it lives here.
"""

from automode.base import AutomodeAdapter, AutomodeExecutionResult
from automode.claude_code import ClaudeCodeAutomodeAdapter
from automode.server import BENCHMARK_NAME, ServerAutomodeAdapter

__all__ = [
    "BENCHMARK_NAME",
    "AutomodeAdapter",
    "AutomodeExecutionResult",
    "ClaudeCodeAutomodeAdapter",
    "ServerAutomodeAdapter",
]
