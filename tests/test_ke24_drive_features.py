import hashlib
import json

import pandas as pd
import pytest

from src.pipeline import run_ke24_drive_features as phase2


def _manifest_record():
    return {
        "_source_file_sha256": "TKN_" + "a" * 32,
        "company_code": "TKN_" + "b" * 32,
        "period": 3,
        "period_year": "03.2026",
        "currency": "USD",
        "record_count": 1,
    }


def test_manifest_signature_detects_divergence():
    actual = pd.DataFrame([_manifest_record()])
    changed = actual.copy()
    changed.loc[0, "record_count"] = 2

    assert phase2._manifest_signature(actual) != phase2._manifest_signature(changed)


def test_no_published_manifest_is_blocked(monkeypatch):
    monkeypatch.setattr(
        phase2,
        "load_processed_manifest",
        lambda service: pd.DataFrame(),
    )
    with pytest.raises(RuntimeError, match="Nenhum lote processed"):
        phase2.load_verified_processed_dataframe(object(), {}, {})


def test_corrupted_parquet_is_blocked_before_read(monkeypatch):
    record = _manifest_record()
    registered = pd.DataFrame([record])
    batch_id = "a" * 16
    good_bytes = b"parquet_valid_for_hash_only"
    document = {
        "schema_version": 2,
        "files": [record],
        "batches": [{
            "batch_id": batch_id,
            "parquet_id": "parquet_1",
            "parquet_name": f"ke24_processed_{batch_id}.parquet",
            "parquet_sha256": hashlib.sha256(good_bytes).hexdigest(),
            "byte_count": len(good_bytes),
            "record_count": 1,
            "source_file_ids": [record["_source_file_sha256"]],
        }],
    }
    manifest_bytes = json.dumps(document).encode("utf-8")

    class Request:
        def __init__(self, contents):
            self.contents = contents

        def execute(self):
            return self.contents

    class Files:
        def get_media(self, fileId):
            return Request(
                manifest_bytes if fileId == "manifest_1" else b"tampered_parquet"
            )

    class FakeService:
        def files(self):
            return Files()

    monkeypatch.setattr(
        phase2, "load_processed_manifest", lambda service: registered
    )
    monkeypatch.setattr(
        phase2,
        "list_processed_files",
        lambda service: [
            {"id": "manifest_1", "name": "ke24_ingestion_manifest.json"},
            {"id": "parquet_1", "name": f"ke24_processed_{batch_id}.parquet"},
        ],
    )

    with pytest.raises(RuntimeError, match="integridade"):
        phase2.load_verified_processed_dataframe(FakeService(), {}, {})
