import ast

import pytest

from tools.integrate_ke24_parallel_raw_audit import (
    MARKER,
    OLD_BLOCK,
    ORIGINAL_ENTRY,
    install,
    transform_source,
)


def fixture_source():
    return (
        "def audit_drive_sources(service, secret_key, *, list_raw=None, download=None, read_manifest=None, calculate_hash=None, tokenize=None):\n"
        + ORIGINAL_ENTRY
        + "        list_raw = list_raw or object()\n"
        + "        download = download or object()\n"
        + "    source_files = []\n"
        + "    committed = set()\n"
        + OLD_BLOCK
        + "    missing = raw_tokens - committed\n"
        + "    return missing\n"
    )


def test_installer_changes_only_expected_code_and_preserves_fallback():
    transformed, changed = transform_source(fixture_source())
    assert changed
    assert MARKER in transformed
    assert "if use_parallel:" in transformed
    assert "if list_raw is None" in transformed
    assert "else:\n        raw_tokens = set()" in transformed
    assert "missing = raw_tokens - committed" in transformed
    ast.parse(transformed)


def test_installer_is_idempotent():
    transformed, changed = transform_source(fixture_source())
    again, changed_again = transform_source(transformed)
    assert changed is True
    assert changed_again is False
    assert transformed == again


def test_installer_refuses_divergent_source():
    with pytest.raises(ValueError):
        transform_source("def run_ke24():\n    return 1\n")


def test_check_never_modifies_file_and_apply_does(tmp_path):
    target = tmp_path / "run_ke24.py"
    initial = fixture_source()
    target.write_text(initial, encoding="utf-8")
    assert install(target, apply=False)
    assert target.read_text(encoding="utf-8") == initial
    assert install(target, apply=True)
    assert MARKER in target.read_text(encoding="utf-8")
    assert not install(target, apply=True)


def test_no_backup_or_temp_left_after_success(tmp_path):
    path = tmp_path / "run_ke24.py"
    path.write_text(fixture_source(), encoding="utf-8")
    install(path, apply=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run_ke24.py"]
