import hashlib
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest

from src.pipeline import run_ke24


def _fixture_repo(tmp_path):
    for _, filename in run_ke24.STEPS:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# stub\n", encoding="utf-8")
    for filename in run_ke24.PREVIEW_FILES:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# stub\n", encoding="utf-8")
    return tmp_path


def _env():
    return {
        "KE24_DRIVE_RAW_FOLDER_ID": "raw-existing",
        "KE24_DRIVE_PROCESSED_FOLDER_ID": "processed-existing",
        "KE24_DRIVE_ALERTS_FOLDER_ID": "alerts-existing",
    }


def _source_functions(file_bytes, processed_tokens):
    names = list(file_bytes)
    def list_raw(_service):
        return [{"id": name} for name in names]
    def download(_service, file_id):
        return BytesIO(file_bytes[file_id])
    def read_manifest(_service):
        return pd.DataFrame({"_source_file_sha256": processed_tokens})
    def calculate_hash(buffer):
        return hashlib.sha256(buffer.read()).hexdigest()
    def tokenize(**kwargs):
        return "TKN_" + kwargs["value"]
    return dict(
        list_raw=list_raw,
        download=download,
        read_manifest=read_manifest,
        calculate_hash=calculate_hash,
        tokenize=tokenize,
    )


def _token(data):
    return "TKN_" + hashlib.sha256(data).hexdigest()


def test_source_gate_all_files_committed():
    files = {"remote-a": b"A", "remote-b": b"B"}
    kwargs = _source_functions(files, [_token(b"A"), _token(b"B")])
    report = run_ke24.audit_drive_sources(object(), b"fake-key", **kwargs)
    assert report == {"raw_files": 2, "processed_files": 2}


def test_source_gate_blocks_unpublished_raw():
    files = {"remote-a": b"A", "remote-b": b"B"}
    kwargs = _source_functions(files, [_token(b"A")])
    with pytest.raises(run_ke24.PreviewBlocked, match="fora do manifesto"):
        run_ke24.audit_drive_sources(object(), b"fake-key", **kwargs)


def test_source_gate_blocks_duplicate_file_content():
    files = {"remote-a": b"A", "remote-b": b"A"}
    kwargs = _source_functions(files, [_token(b"A")])
    with pytest.raises(run_ke24.PreviewBlocked, match="duplicado"):
        run_ke24.audit_drive_sources(object(), b"fake-key", **kwargs)


def test_source_gate_blocks_empty_manifest():
    kwargs = _source_functions({"remote-a": b"A"}, [])
    with pytest.raises(run_ke24.PreviewBlocked, match="vazio"):
        run_ke24.audit_drive_sources(object(), b"fake-key", **kwargs)


def test_readonly_stages_do_not_repeat_ml_or_write_anything():
    steps = []
    processed = pd.DataFrame({"safe": [1, 2]})
    incoming = pd.DataFrame({"dummy": [1, 2]})
    previous = pd.DataFrame({"dummy": [3]})
    combined = pd.DataFrame({"dummy": [3, 1, 2]})

    def record(name, result):
        def fn(*args, **kwargs):
            steps.append(name)
            return result
        return fn

    adapters = SimpleNamespace(
        load_processed=record("load_processed", processed),
        build_features=record("features", (incoming, "manifest", {"train_rows": 1, "validation_rows": 1})),
        build_alert_preview=record("ml_preview", {"candidates": incoming, "candidate_count": 2, "stable_all_seeds": 1}),
        build_pending_outputs=record("pending_alerts", {"history": incoming}),
        load_history=record("load_history", SimpleNamespace(history=previous)),
        append_alerts=record("merge_history", (combined, 2)),
        sheet_matrix=record("sheet_matrix", [["header"], [1], [2], [3]]),
    )
    result = run_ke24.execute_readonly_stages(
        object(), {}, {}, b"key", adapters=adapters,
        source_audit=record("audit_sources", {"raw_files": 2, "processed_files": 2}),
    )
    assert steps == [
        "audit_sources", "load_processed", "features", "ml_preview", "pending_alerts",
        "load_history", "merge_history", "sheet_matrix",
    ]
    assert result["candidate_count"] == 2
    assert result["sheet_rows"] == 3
    assert result["history_new"] == 2


def test_readonly_stages_support_no_alerts():
    df = pd.DataFrame({"safe": []})
    adapters = SimpleNamespace(
        load_processed=lambda *a: df,
        build_features=lambda *a: (df, df, {"train_rows": 1, "validation_rows": 1}),
        build_alert_preview=lambda *a: {"candidates": df, "candidate_count": 0, "stable_all_seeds": 0},
        build_pending_outputs=lambda *a: {"history": df},
        load_history=lambda *a: SimpleNamespace(history=df),
        append_alerts=lambda *a: (df, 0),
        sheet_matrix=lambda *a: pytest.fail("Nao exportar historico vazio"),
    )
    res = run_ke24.execute_readonly_stages(
        object(), {}, {}, b"key", adapters=adapters,
        source_audit=lambda *a: {"raw_files": 2, "processed_files": 2},
    )
    assert res["sheet_rows"] == 0


def test_preview_missing_env_never_authenticates(tmp_path, monkeypatch):
    _fixture_repo(tmp_path)
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: pytest.fail("Nao conectar"))
    assert run_ke24.run_preview(tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "configured"}) == 1


def test_preview_complete_invokes_single_workflow(tmp_path, monkeypatch):
    _fixture_repo(tmp_path)
    invocations = []
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", "policy", "schema", "key"))
    monkeypatch.setattr(
        run_ke24, "execute_readonly_stages",
        lambda *a: invocations.append(a) or {"candidate_count": 1},
    )
    assert run_ke24.run_preview(tmp_path, _env()) == 0
    assert invocations == [("service", "policy", "schema", "key")]


def test_preview_remote_failure_returns_error(tmp_path, monkeypatch):
    _fixture_repo(tmp_path)
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", "policy", "schema", "key"))
    monkeypatch.setattr(
        run_ke24, "execute_readonly_stages", lambda *a: (_ for _ in ()).throw(RuntimeError("remote failed"))
    )
    assert run_ke24.run_preview(tmp_path, _env()) == 1


def test_main_invalid_flag_stops_before_cloud(monkeypatch):
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: pytest.fail("Nao conectar"))
    with pytest.raises(SystemExit):
        run_ke24.main(["--publish"])


def test_source_failure_stops_before_processed_load():
    def deny(*args):
        raise run_ke24.PreviewBlocked("Dados novos no Drive")
    adapters = SimpleNamespace(
        load_processed=lambda *args: pytest.fail("Nao carregar processed apos bloqueio"),
    )
    with pytest.raises(run_ke24.PreviewBlocked):
        run_ke24.execute_readonly_stages(
            object(), {}, {}, b"key", adapters=adapters, source_audit=deny
        )
