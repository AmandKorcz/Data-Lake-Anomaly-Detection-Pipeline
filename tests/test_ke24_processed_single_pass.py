import hashlib
import json
from collections import Counter

import pandas as pd
import pytest

from src.pipeline import run_ke24_drive_features as feature


SRC = "TKN_" + "a" * 32
COMP = "TKN_" + "b" * 32


def _fixture(monkeypatch, *, parquet_bytes=b"parquet-ok", tamper=False, add_orphan=False,
             change_manifest=False, change_listing=False):
    entry = {
        "_source_file_sha256": SRC,
        "company_code": COMP,
        "period": 3,
        "period_year": "03.2026",
        "currency": "USD",
        "record_count": 1,
    }
    parquet_name = "ke24_processed_" + "a" * 16 + ".parquet"
    manifest_name = "ke24_ingestion_manifest.json"
    manifest_id = "manifest-synthetic"
    parquet_id = "parquet-synthetic"
    document = {
        "schema_version": 2,
        "files": [entry],
        "batches": [{
            "batch_id": "a" * 16,
            "parquet_id": parquet_id,
            "parquet_name": parquet_name,
            "parquet_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            "byte_count": len(parquet_bytes),
            "record_count": 1,
            "source_file_ids": [SRC],
        }],
    }
    source_manifest = json.dumps(document).encode("utf-8")
    counts = Counter()

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Files:
        def get_media(self, fileId):
            counts[("get_media", fileId)] += 1
            if fileId == manifest_id:
                if change_manifest and counts[("get_media", manifest_id)] == 2:
                    return Request(source_manifest + b" ")
                return Request(source_manifest)
            if fileId == parquet_id:
                return Request(b"not-the-original" if tamper else parquet_bytes)
            raise AssertionError("Unexpected Drive ID")

    class Service:
        def files(self):
            return Files()

    listings = [
        {"id": manifest_id, "name": manifest_name},
        {"id": parquet_id, "name": parquet_name},
    ]
    if add_orphan:
        listings.append({"id": "unregistered", "name": "unregistered.parquet"})

    def fake_list(service):
        counts["list"] += 1
        if change_listing and counts["list"] == 2:
            return [*listings, {"id": "late", "name": "new.parquet"}]
        return listings

    frame = pd.DataFrame({
        "_source_file_sha256": [SRC],
        "calendar_year": [2026],
        "period_key": ["2026-03"],
        "period_date": ["2026-03-01"],
    })

    def read_parquet(buffer, *, engine):
        counts["read_parquet"] += 1
        assert engine == "pyarrow"
        assert buffer.read() == parquet_bytes
        return frame.copy()

    monkeypatch.setattr(feature, "list_processed_files", fake_list)
    monkeypatch.setattr(feature.pd, "read_parquet", read_parquet)
    monkeypatch.setattr(feature, "validate_protected_dataset", lambda *a, **kw: None)
    monkeypatch.setattr(feature, "build_file_manifest", lambda data: pd.DataFrame([entry]))

    privacy_policy = {"metadata_actions": {}}
    privacy_schema = {"columns": ["_source_file_sha256"]}
    return Service(), privacy_policy, privacy_schema, counts


def test_single_pass_downloads_each_parquet_once_and_checks_manifest_twice(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch)
    processed = feature.load_verified_processed_dataframe(service, policy, schema)
    assert len(processed) == 1
    assert counts[("get_media", "parquet-synthetic")] == 1
    assert counts[("get_media", "manifest-synthetic")] == 2
    assert counts["list"] == 2
    assert counts["read_parquet"] == 1


def test_bad_parquet_hash_blocks_before_parse(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch, tamper=True)
    with pytest.raises(RuntimeError, match="integridade"):
        feature.load_verified_processed_dataframe(service, policy, schema)
    assert counts["read_parquet"] == 0


def test_mutation_of_manifest_mid_read_blocks(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch, change_manifest=True)
    with pytest.raises(RuntimeError, match="Manifesto foi alterado"):
        feature.load_verified_processed_dataframe(service, policy, schema)
    assert counts[("get_media", "parquet-synthetic")] == 1


def test_mutation_of_listing_mid_read_blocks(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch, change_listing=True)
    with pytest.raises(RuntimeError, match="Pasta processed alterada"):
        feature.load_verified_processed_dataframe(service, policy, schema)
    assert counts[("get_media", "parquet-synthetic")] == 1


def test_unregistered_parquet_blocks_before_download(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch, add_orphan=True)
    with pytest.raises(RuntimeError, match="Arquivos do processed divergentes"):
        feature.load_verified_processed_dataframe(service, policy, schema)
    assert counts[("get_media", "parquet-synthetic")] == 0


def test_duplicate_remote_entry_blocks_before_download(monkeypatch):
    service, policy, schema, counts = _fixture(monkeypatch)
    original = feature.list_processed_files
    def duplicated(service):
        listed = original(service)
        return [*listed, listed[-1]]
    monkeypatch.setattr(feature, "list_processed_files", duplicated)
    with pytest.raises(RuntimeError, match="Arquivos do processed divergentes"):
        feature.load_verified_processed_dataframe(service, policy, schema)
    assert counts[("get_media", "parquet-synthetic")] == 0


def test_missing_processed_files_blocks(monkeypatch):
    monkeypatch.setattr(feature, "list_processed_files", lambda service: [])
    with pytest.raises(RuntimeError, match="Nenhum lote processed"):
        feature.load_verified_processed_dataframe(object(), {}, {})
