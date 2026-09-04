"""Force-pushing over a protected branch.

Denies only when the overwritten branch is determinable from the command
line and protected. When the refspec names the branch indirectly -- HEAD,
@, or no refspec at all -- the branch is repo state stage 1 does not have,
so the answer is ask: not a denial it has not earned, and not a silence
that would let stage 2 decide on less information than stage 1 had.

A determinable protected branch is a certainty and outranks ambiguity
found elsewhere in the same action, so an ask is held back until every
command has been scanned.
"""

import fnmatch
import os

from agentgate.api.schemas import DecisionKind
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.rules.hard_deny.shared import effective_argv

_AMBIGUOUS_ID = "ambiguous.git-force"
# git global options that take a following value, e.g. `git -C <path> push
# ...` — skipped (option + value) while looking for the "push" subcommand
# so it is not missed just because it is not argv[1].
_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# Refspecs that name a branch only indirectly: whatever the local checkout
# currently points at, which may well be protected. Not literal branch
# names, so they cannot be matched against protected_branches at all.
_SYMBOLIC_REFS = {"HEAD", "@"}


class GitForceRule:
    id = "hard-deny.git-force"
    hard = True

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        pending_ask: Verdict | None = None
        for command in action.commands:
            push_argv = _push_argv(effective_argv(command.argv))
            if push_argv is None:
                continue
            verdict = self._for_push(push_argv[1:], policy)
            if verdict is None:
                continue
            if verdict.decision is DecisionKind.deny:
                return verdict
            pending_ask = pending_ask or verdict
        return pending_ask

    def _for_push(self, rest: list[str], policy: Policy) -> Verdict | None:
        if "--dry-run" in rest:
            return None  # changes nothing, by definition — never flagged
        positionals = [a for a in rest if not a.startswith("-")]
        forced = any(_is_force_flag(a) for a in rest if a.startswith("-"))
        if len(positionals) >= 2:
            # First positional is the remote, the rest are refspecs.
            return self._for_refspecs(positionals[1:], forced, policy)
        if forced:
            # Either no positional at all, or a single one that cannot be
            # told apart between "remote" (pushes the current branch) and
            # "branch" (remote implied) without repo state.
            return Verdict.ask(
                _AMBIGUOUS_ID,
                "force push with no identifiable refspec — cannot determine whether the current branch is protected",
                "Specify the target branch explicitly, e.g. `git push --force origin <branch>`",
            )
        return None

    def _for_refspecs(self, refs: list[str], forced: bool, policy: Policy) -> Verdict | None:
        pending_ask: Verdict | None = None
        for ref in refs:
            if not (forced or ref.startswith("+")):
                continue  # this particular ref isn't being force-pushed
            bare = ref[1:] if ref.startswith("+") else ref
            if ":" not in bare and bare in _SYMBOLIC_REFS:
                # Two positionals make the command LOOK determinable, but
                # arity is not knowledge. "HEAD:branch" is not ambiguous:
                # the destination is spelled out and is what gets
                # overwritten, so it falls through to the match below.
                pending_ask = pending_ask or Verdict.ask(
                    _AMBIGUOUS_ID,
                    f"force push of '{bare}' — the branch it currently points at is repo state, "
                    "not something the command line states",
                    "Name the target branch explicitly, e.g. `git push --force origin <branch>`",
                )
                continue
            branch = _destination_branch(ref)
            if any(fnmatch.fnmatchcase(branch, pattern) for pattern in policy.protected_branches):
                return Verdict.deny(
                    self.id, f"force push to protected branch {branch}",
                    "Push to a feature branch", hard=True,
                )
        return pending_ask


def _push_argv(argv: list[str]) -> list[str] | None:
    """Return argv starting at "push" if this git invocation's subcommand is
    push (after skipping any global options), else None.
    """
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "push":
            return argv[i:]
        if not tok.startswith("-"):
            return None  # some other subcommand
        if tok in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        i += 1
    return None


def _is_force_flag(a: str) -> bool:
    if a in ("--force", "-f", "--force-with-lease") or a.startswith(("--force=", "--force-with-lease=")):
        return True
    # Combined short-option clusters, e.g. "-fu", "-uf": any single-dash,
    # non-long-form token whose letters include "f" carries force, matching
    # git's own short-option bundling.
    return a.startswith("-") and not a.startswith("--") and len(a) > 1 and "f" in a[1:]


def _destination_branch(ref: str) -> str:
    branch = ref[1:] if ref.startswith("+") else ref  # leading "+" is per-ref force syntax
    branch = branch.split(":")[-1]  # src:dest refspec — the destination is what's overwritten
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    return branch
