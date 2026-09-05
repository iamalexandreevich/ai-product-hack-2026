"""The detectors in the order they are tried on a line: the first match wins."""

from agentgate.inspect.detectors import ENCODED, INJECTION, INVISIBLE, PIPE_EXEC

INSPECT_STAGE1 = (INJECTION, PIPE_EXEC, ENCODED, INVISIBLE)
