from agentgate.shell.wrappers import WRAPPER_COMMANDS, chain_unresolved, resolve_effective_argv


def test_a_plain_command_resolves_to_itself():
    assert resolve_effective_argv(["rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_a_wrapper_resolves_to_the_command_behind_it():
    assert resolve_effective_argv(["env", "rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_a_boolean_wrapper_flag_does_not_consume_the_command():
    assert resolve_effective_argv(["env", "-i", "rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_a_wrapper_option_value_in_a_separate_token_is_skipped():
    assert resolve_effective_argv(["nice", "-n", "10", "rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_env_assignments_are_skipped():
    assert resolve_effective_argv(["env", "FOO=bar", "rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_the_timeout_duration_positional_is_skipped():
    assert resolve_effective_argv(["timeout", "30", "curl", "http://x"]) == ["curl", "http://x"]


def test_wrappers_chain():
    assert resolve_effective_argv(["env", "nohup", "rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_a_bare_wrapper_resolves_to_nothing():
    assert resolve_effective_argv(["env"]) == []


def test_a_chain_deeper_than_the_bound_still_starts_with_a_wrapper():
    argv = ["env"] * 9 + ["rm", "-rf", "/"]
    assert resolve_effective_argv(argv)[0] == "env"


def test_a_wrapper_outside_the_given_set_is_left_alone():
    assert resolve_effective_argv(["sudo", "rm"], WRAPPER_COMMANDS - {"sudo"}) == ["sudo", "rm"]


def test_chain_unresolved_reports_depth_for_a_chain_deeper_than_the_bound():
    argv = ["env"] * 9 + ["rm", "-rf", "/"]
    assert chain_unresolved(argv, WRAPPER_COMMANDS) == "depth"


def test_chain_unresolved_says_nothing_about_a_plain_command():
    assert chain_unresolved(["rm", "-rf", "/"], WRAPPER_COMMANDS) is None


def test_chain_unresolved_reports_opaque_when_the_command_lands_inside_an_option():
    assert chain_unresolved(["env", "-S", "rm -rf /"], WRAPPER_COMMANDS) == "opaque"


def test_chain_unresolved_says_nothing_about_a_bare_wrapper():
    assert chain_unresolved(["env"], WRAPPER_COMMANDS) is None


def test_chain_unresolved_says_nothing_about_a_wrapper_carrying_only_boolean_flags():
    assert chain_unresolved(["env", "-i"], WRAPPER_COMMANDS) is None
