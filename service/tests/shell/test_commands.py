"""What the command table promises: a leaf import, a neutral answer for
an unknown command, and one row per command that adding a command edits.
"""

import subprocess
import sys

from agentgate.shell.commands import (
    COMMANDS,
    CommandSpec,
    PathArguments,
    Role,
    WriteTarget,
    spec_for,
)
from agentgate.shell.paths import PathRole, command_paths


def test_the_table_can_be_imported_before_anything_else():
    """The table has to stay a leaf. Reaching into agentgate.normalize from
    it runs that package's eager init, which imports the normalizer, which
    imports the table back -- a cycle that fires for whoever imports the
    table first and for nobody else.
    """
    done = subprocess.run(
        [sys.executable, "-c", "import agentgate.shell.commands"],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr


def test_an_unknown_command_gets_a_neutral_spec():
    spec = spec_for("some-tool-we-have-never-seen")
    assert spec.roles == frozenset()


def test_an_unknown_command_writes_nothing():
    assert spec_for("some-tool-we-have-never-seen").write_target is WriteTarget.NONE


def test_an_unknown_commands_arguments_are_not_declared_paths():
    spec = spec_for("some-tool-we-have-never-seen")
    assert spec.path_arguments is PathArguments.UNDECLARED


def test_curl_declares_the_flags_that_write_a_file():
    flags = spec_for("curl").output_flags
    assert {"-o", "--output", "-O", "--remote-name", "--output-dir"} <= flags


def test_wget_declares_the_flags_that_write_a_file():
    flags = spec_for("wget").output_flags
    assert {
        "-O", "--output-document", "-P", "--directory-prefix",
        "-o", "--output-file",
    } <= flags


def test_a_command_with_no_row_declares_no_output_flags():
    assert spec_for("definitely-not-a-command").output_flags == frozenset()


def test_a_reading_command_declares_no_output_flags():
    assert spec_for("cat").output_flags == frozenset()


def test_a_new_command_is_one_row():
    table = {**COMMANDS, "shred-plus": CommandSpec(
        name="shred-plus",
        roles=frozenset({Role.MUTATING}),
        write_target=WriteTarget.EVERY_POSITIONAL,
        path_arguments=PathArguments.EVERY_POSITIONAL,
    )}
    assert Role.MUTATING in table["shred-plus"].roles


def test_any_role_resolves_every_non_flag_argument():
    paths = command_paths(["diff", "-u", "AGENTS.md", "b.md"], "/w", PathRole.ANY)
    assert paths == ("/w/AGENTS.md", "/w/b.md")


def test_write_role_of_a_copy_is_its_destination_only():
    assert command_paths(["cp", "a.txt", "out.txt"], "/w", PathRole.WRITE) == ("/w/out.txt",)


def test_write_role_of_tee_is_every_destination():
    paths = command_paths(["tee", "-a", "a.log", "b.log"], "/w", PathRole.WRITE)
    assert paths == ("/w/a.log", "/w/b.log")


def test_write_role_of_sed_is_empty_without_in_place():
    assert command_paths(["sed", "s/a/b/", "f.txt"], "/w", PathRole.WRITE) == ()


def test_write_role_of_sed_in_place_skips_the_script():
    paths = command_paths(["sed", "-i", "s/a/b/", "f.txt"], "/w", PathRole.WRITE)
    assert paths == ("/w/f.txt",)


def test_write_role_of_an_unknown_command_is_empty():
    assert command_paths(["some-tool", "a", "b"], "/w", PathRole.WRITE) == ()


def test_write_only_role_excludes_an_in_place_edit():
    argv = ["sed", "-i", "s/a/b/", "f.txt"]
    assert command_paths(argv, "/w", PathRole.WRITE_ONLY) == ()


def test_write_only_role_still_holds_a_plain_destination():
    argv = ["cp", "a.txt", "out.txt"]
    assert command_paths(argv, "/w", PathRole.WRITE_ONLY) == ("/w/out.txt",)
