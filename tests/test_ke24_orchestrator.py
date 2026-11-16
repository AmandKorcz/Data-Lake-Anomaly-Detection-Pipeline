from pathlib import Path

from src.pipeline.run_ke24 import STEPS, inspect_project, run_dry_run


def build_fake_repo(root: Path) -> None:
    for _stage, paths in STEPS:
        for rel in paths:
            file = root / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("# fake fixture\n", encoding="utf-8")


def test_dry_run_structure_ok_without_real_data(tmp_path):
    build_fake_repo(tmp_path)
    report = inspect_project(tmp_path)
    assert report["structural_ok"] is True
    assert all(not present for present in report["artifacts"].values())
    assert run_dry_run(tmp_path) == 0


def test_dry_run_reports_missing_component_without_writing(tmp_path):
    build_fake_repo(tmp_path)
    missing_file = tmp_path / STEPS[0][1][0]
    missing_file.unlink()
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    report = inspect_project(tmp_path)
    assert report["structural_ok"] is False
    assert report["steps"][0]["missing"] == [STEPS[0][1][0]]
    assert run_dry_run(tmp_path) == 1
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert before == after


def test_dry_run_ignores_local_raw_sources(tmp_path):
    build_fake_repo(tmp_path)
    raw = tmp_path / "data/raw"
    raw.mkdir(parents=True)
    (raw / "ke24_dummy.xlsx").write_bytes(b"not even valid XLSX")
    report = inspect_project(tmp_path)
    assert "raw_xlsx_count" not in report
    assert report["structural_ok"] is True
