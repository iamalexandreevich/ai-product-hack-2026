from agentgate.shell.secrets import SECRET_PATTERNS, is_secret_path

WORKSPACE = "/home/u/repo"


def test_dotenv_is_secret():
    assert is_secret_path("/home/u/repo/.env", WORKSPACE)


def test_private_key_is_secret():
    assert is_secret_path("/home/u/repo/deploy.pem", WORKSPACE)


def test_ssh_directory_is_secret():
    assert is_secret_path("/home/u/.ssh/id_rsa", WORKSPACE)


def test_ordinary_source_file_is_not_secret():
    assert not is_secret_path("/home/u/repo/main.py", WORKSPACE)


def test_patterns_are_immutable():
    assert isinstance(SECRET_PATTERNS, tuple)


def test_npmrc_is_secret():
    """Only the path-side list knew about .npmrc, so an upload of it was
    path-shaped but never secret. The merged list closes that.
    """
    assert is_secret_path("/home/u/repo/.npmrc", WORKSPACE)


def test_pkcs12_bundle_is_secret():
    """Only the exfil-side list knew about *.p12, so a bare "cert.p12"
    argument did not even look like a path to the normalizer.
    """
    assert is_secret_path("/home/u/repo/cert.p12", WORKSPACE)


def test_a_bare_basename_is_matched_without_a_workspace():
    """looks_like_path asks about a slash-free argv token, which has no
    workspace to be relative to.
    """
    assert is_secret_path("id_rsa", None)
