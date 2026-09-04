from agentgate.shell.argv import ParsedArgv

VALUE_FLAGS = frozenset({"-o", "--output", "-T", "--upload-file"})


def test_executable_is_the_first_token():
    assert ParsedArgv.of(["curl", "-s", "http://x"]).executable == "curl"


def test_positionals_exclude_flags():
    assert ParsedArgv.of(["cp", "-r", "a", "b"]).positionals == ("a", "b")


def test_separate_value_is_not_a_positional():
    parsed = ParsedArgv.of(["curl", "-T", "secret.txt", "http://x"], VALUE_FLAGS)
    assert parsed.positionals == ("http://x",)


def test_separate_value_is_reachable_by_flag_name():
    parsed = ParsedArgv.of(["curl", "-T", "secret.txt", "http://x"], VALUE_FLAGS)
    assert parsed.option("-T").value == "secret.txt"


def test_inline_long_value_is_parsed():
    parsed = ParsedArgv.of(["curl", "--output=out.txt"], VALUE_FLAGS)
    assert parsed.option("--output").value == "out.txt"


def test_inline_value_is_marked_inline():
    assert ParsedArgv.of(["curl", "--output=out.txt"], VALUE_FLAGS).option("--output").inline


def test_missing_option_is_none():
    assert ParsedArgv.of(["curl", "http://x"]).option("-T") is None


def test_has_reports_a_valueless_flag():
    assert ParsedArgv.of(["rm", "-rf", "/"]).has("-rf")


def test_values_of_collects_every_named_flag():
    parsed = ParsedArgv.of(["curl", "-T", "a", "-T", "b"], VALUE_FLAGS)
    assert parsed.values_of("-T") == ("a", "b")


def test_double_dash_ends_option_parsing():
    parsed = ParsedArgv.of(["rm", "--", "-weird-file"])
    assert parsed.positionals == ("-weird-file",)


def test_empty_argv_has_no_executable():
    assert ParsedArgv.of([]).executable == ""
