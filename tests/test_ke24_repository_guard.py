import subprocess

import pytest

from src.security.ke24_repository_guard import (
    EXPECTED_IGNORED,
    RepositoryGuardError,
    check_repository,
    indexed_paths,
    is_ignored,
    is_private_path,
)


@pytest.mark.parametrize("filename", [
    "config/ke24.keys.env", "config/other.keys.env",
    "config/ke24.local.env", "config/ke24.private.env", "config/.env",
    ".env.production", "private/.env", "secrets/key.txt",
    "credentials/oauth.json", "oauth-client.json", "drive-token.json",
    "drive-write-token.json", "token.json", "client_secret_demo.json",
    "config/service-account-demo.json", "config/credentials-dev.json",
    "anything.pem", "secret.key", "CONFIG\\TEAM.KEYS.ENV",
])
def test_recognizes_sensitive_filename(filename):
    assert is_private_path(filename)


@pytest.mark.parametrize("filename", [
    "src/security/ke24_repository_guard.py",
    "tests/test_ke24_hmac_key.py", "docs/secret_rotation_guide.md",
    "config/ke24_schema.json", "config/ke24_alert_rules.example.json",
    "README.md", ".gitignore", "config/example.env.sample",
])
def test_allows_regular_tracked_files(filename):
    assert not is_private_path(filename)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


@pytest.fixture
def repo(tmp_path):
    try:
        _git(tmp_path, "init", "-q")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("Git nao disponivel neste ambiente de testes")
    (tmp_path / ".gitignore").write_text(
        ".env\n.env.*\n*.keys.env\n*.local.env\n*.private.env\n"
        "oauth-client.json\ndrive-token.json\n"
        "client_secret*.json\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".gitignore")
    return tmp_path


def test_local_private_file_remains_ignored_and_untracked(repo):
    folder = repo / "config"
    folder.mkdir()
    (folder / "ke24.keys.env").write_text("ONLY_SYNTHETIC_TEST_VALUE=1\n", encoding="utf-8")
    assert "config/ke24.keys.env" not in indexed_paths(repo)
    assert is_ignored(repo, "config/ke24.keys.env")
    assert check_repository(repo).approved


def test_forced_add_is_detected_even_if_gitignore_matches(repo):
    folder = repo / "config"
    folder.mkdir()
    (folder / "ke24.keys.env").write_text("ONLY_SYNTHETIC_TEST_VALUE=1\n", encoding="utf-8")
    _git(repo, "add", "-f", "config/ke24.keys.env")
    result = check_repository(repo)
    assert not result.approved
    assert result.tracked_private_count == 1
    assert result.missing_ignore_count == 0


def test_missing_pattern_is_reported(repo):
    (repo / ".gitignore").write_text(".env\n.env.*\n", encoding="utf-8")
    result = check_repository(repo)
    assert not result.approved
    assert result.missing_ignore_count > 0


def test_empty_directory_without_git_blocks(tmp_path):
    with pytest.raises(RepositoryGuardError):
        check_repository(tmp_path)


def test_check_does_not_create_any_files(repo):
    old = {x.relative_to(repo) for x in repo.iterdir()}
    assert check_repository(repo).approved
    assert {x.relative_to(repo) for x in repo.iterdir()} == old


def test_all_expectations_have_private_names():
    assert all(is_private_path(name) for name in EXPECTED_IGNORED)
