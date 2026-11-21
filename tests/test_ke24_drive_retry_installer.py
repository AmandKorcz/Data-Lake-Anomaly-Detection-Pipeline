import ast
from pathlib import Path

import pytest

from tools.integrate_ke24_drive_retry import CHANGES, IMPORT, MARKER, install, plan, transform_source


def fixture_sources():
    raw_block = CHANGES["src/ingestion/google_drive_client.py"][0][0]
    processed_blocks = CHANGES["src/loading/ke24_drive_manifest.py"]
    feature_blocks = CHANGES["src/pipeline/run_ke24_drive_features.py"]
    return {
        "src/ingestion/google_drive_client.py": (
            "def list_raw_xlsx_files(service):\n"
            "    query = ''\n    page_token = None\n    while True:\n"
            + raw_block + "\n        break\n"
        ),
        "src/loading/ke24_drive_manifest.py": (
            "def list_processed_files(service):\n"
            "    folder_id = ''\n    page_token = None\n    while True:\n"
            + processed_blocks[0][0] + "\n        break\n\n"
            "def load_processed_manifest(service):\n"
            "    manifests = [{'id': 'synthetic'}]\n"
            + processed_blocks[1][0] + "\n"
            "    for batch in [{'parquet_id': 'synthetic'}]:\n"
            + processed_blocks[2][0] + "\n"
        ),
        "src/pipeline/run_ke24_drive_features.py": (
            "def load_verified_processed_dataframe(service):\n"
            "    manifest_files = [{'id': 'synthetic'}]\n"
            + feature_blocks[0][0] + "\n"
            "    for parquet_id in ['synthetic']:\n"
            + feature_blocks[1][0] + "\n"
        ),
    }


@pytest.mark.parametrize("path", list(CHANGES))
def test_transformation_is_idempotent(path):
    source = fixture_sources()[path]
    transformed, changed = transform_source(source, CHANGES[path])
    assert changed
    ast.parse(transformed)
    assert transformed.count(MARKER) == 1
    assert transformed.count(IMPORT) == 1
    assert transform_source(transformed, CHANGES[path]) == (transformed, False)


@pytest.mark.parametrize("path", list(CHANGES))
def test_refuses_partial_installation(path):
    source = fixture_sources()[path]
    old, new = CHANGES[path][0]
    corrupted = source.replace(old, new)
    with pytest.raises(ValueError):
        transform_source(corrupted, CHANGES[path])


def test_installer_check_is_readonly_then_apply_is_idempotent(tmp_path):
    for relative, source in fixture_sources().items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    assert install(tmp_path, apply=False) == 3
    assert all((tmp_path / rel).read_text(encoding="utf-8") == content
               for rel, content in fixture_sources().items())
    assert install(tmp_path, apply=True) == 3
    assert install(tmp_path, apply=False) == 0
    assert install(tmp_path, apply=True) == 0


def test_preflight_rejects_divergence_without_touching_any_file(tmp_path):
    sources = fixture_sources()
    for relative, source in sources.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    broken = tmp_path / "src/loading/ke24_drive_manifest.py"
    broken.write_text("def unrelated():\n    return 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        install(tmp_path, apply=True)
    assert (tmp_path / "src/ingestion/google_drive_client.py").read_text(encoding="utf-8") == sources["src/ingestion/google_drive_client.py"]


def test_preflight_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        plan(tmp_path)


def test_missing_marker_with_complete_installation_repairs_marker_only():
    path = "src/loading/ke24_drive_manifest.py"
    source = fixture_sources()[path]
    transformed, _ = transform_source(source, CHANGES[path])
    no_marker = transformed.replace(f"# {MARKER}: leituras somente leitura com retry limitado.\n", "")
    fixed, changed = transform_source(no_marker, CHANGES[path])
    assert changed is True
    assert fixed == transformed
