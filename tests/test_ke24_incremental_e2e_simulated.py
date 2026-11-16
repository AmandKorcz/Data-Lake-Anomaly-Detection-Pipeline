from __future__ import annotations

import hashlib
import json
from io import BytesIO

import pandas as pd
import pytest

from src.loading import ke24_drive_incremental_publish as inc
from src.loading import ke24_drive_publish as initial_publish
from src.loading.ke24_drive_manifest import (
    MANIFEST_NAME,
    load_processed_manifest,
    serialize_processed_manifest,
)
from src.loading.ke24_drive_source_selection import collect_new_drive_frames
from src.loading.plan_ke24_incremental import (
    FILE_ID,
    build_file_manifest,
    plan_incremental_load,
)
from src.privacy.anonymize_ke24 import generate_token

KEY = b"K" * 32
COMPANY = "TKN_" + "c" * 32


def protected_id(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return generate_token(digest, FILE_ID, None, KEY)


def manifest_entry(content: bytes, period: int, rows: int) -> dict:
    return {
        FILE_ID: protected_id(content),
        "company_code": COMPANY,
        "period": period,
        "period_year": f"{period:02d}.2026",
        "currency": "USD",
        "record_count": rows,
    }


class Request:
    def __init__(self, operation):
        self.operation = operation

    def execute(self):
        return self.operation()


class Store:
    def __init__(self, objects):
        self.objects = objects
        self.created = 0
        self.updated = 0
        self.fail_update = False
        self.next_id = 100


class Files:
    def __init__(self, store):
        self.store = store

    def list(self, **kwargs):
        return Request(lambda: {
            "files": [
                {"id": fid, "name": value["name"], "mimeType": value["mimeType"]}
                for fid, value in self.store.objects.items()
            ]
        })

    def get_media(self, fileId):
        return Request(lambda: self.store.objects[fileId]["content"])

    def get(self, fileId, fields):
        if fileId == "FOLDER_SIMULATED":
            return Request(lambda: {
                "id": fileId,
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
            })
        return Request(lambda: {
            "id": fileId,
            "trashed": False,
            "capabilities": {"canEdit": True},
        })

    def create(self, body, media_body, fields):
        def operation():
            assert body["name"] != MANIFEST_NAME, "Nao criar um segundo manifesto"
            self.store.next_id += 1
            fid = f"NEW_{self.store.next_id}"
            media_body.stream.seek(0)
            self.store.objects[fid] = {
                "name": body["name"],
                "mimeType": body["mimeType"],
                "content": media_body.stream.read(),
            }
            self.store.created += 1
            return {"id": fid, "name": body["name"], "parents": body["parents"]}
        return Request(operation)

    def update(self, fileId, media_body, fields):
        def operation():
            self.store.updated += 1
            if self.store.fail_update:
                raise ConnectionError("Falha simulada no update do manifesto")
            media_body.stream.seek(0)
            self.store.objects[fileId]["content"] = media_body.stream.read()
            return {"id": fileId, "name": self.store.objects[fileId]["name"]}
        return Request(operation)


class Drive:
    def __init__(self, store):
        self.store = store

    def files(self):
        return Files(self.store)


class FakeUpload:
    def __init__(self, stream, mimetype, resumable):
        self.stream = stream


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.setenv("KE24_DRIVE_PROCESSED_FOLDER_ID", "FOLDER_SIMULATED")
    monkeypatch.setattr(inc, "LOCK_PATH", tmp_path / "publication.lock")
    monkeypatch.setattr(inc, "MediaIoBaseUpload", FakeUpload)
    monkeypatch.setattr(initial_publish, "MediaIoBaseUpload", FakeUpload)

    old_xlsx = b"old-xlsx-synthetic-content"
    new_xlsx = b"new-xlsx-synthetic-content"
    old_parquet = b"parquet-previously-published-synthetic"
    old_record = manifest_entry(old_xlsx, period=1, rows=2)
    old_batch = {
        "batch_id": "a" * 16,
        "parquet_id": "PARQUET_PREVIOUS",
        "parquet_name": "ke24_processed_" + "a" * 16 + ".parquet",
        "parquet_sha256": hashlib.sha256(old_parquet).hexdigest(),
        "byte_count": len(old_parquet),
        "record_count": 2,
        "source_file_ids": [old_record[FILE_ID]],
    }
    manifest_bytes = serialize_processed_manifest(pd.DataFrame([old_record]), [old_batch])
    store = Store({
        "PARQUET_PREVIOUS": {
            "name": old_batch["parquet_name"],
            "mimeType": "application/octet-stream",
            "content": old_parquet,
        },
        "MANIFEST_PREVIOUS": {
            "name": MANIFEST_NAME,
            "mimeType": "application/json",
            "content": manifest_bytes,
        },
    })
    return {
        "read": Drive(store),
        "write": Drive(store),
        "store": store,
        "old_xlsx": old_xlsx,
        "new_xlsx": new_xlsx,
        "old_parquet": old_parquet,
    }


def collect(context, manifest, *, extra_duplicate=False, new_period=2):
    raw = {
        "RAW_OLD": context["old_xlsx"],
        "RAW_NEW": context["new_xlsx"],
    }
    if extra_duplicate:
        raw["RAW_NEW_COPY"] = context["new_xlsx"]
    parse_count = []

    def download(_service, fid):
        return BytesIO(raw[fid])

    def parse(buffer, *, expected_columns, source_name):
        parse_count.append(source_name)
        digest = hashlib.sha256(buffer.getvalue()).hexdigest()
        frame = pd.DataFrame([{
            FILE_ID: digest,
            "_source_file": source_name,
            "_source_row_number": row_number,
            "company_code": COMPANY,
            "period": new_period,
            "period_year": f"{new_period:02d}.2026",
            "currency": "USD",
        } for row_number in range(2, 5)])
        return frame, None, expected_columns, len(frame)

    def anonymize(*, df, policy, schema, secret_key):
        assert secret_key == KEY
        protected = df.copy()
        protected[FILE_ID] = protected[FILE_ID].map(
            lambda digest: generate_token(digest, FILE_ID, None, KEY)
        )
        protected["_source_file"] = "TKN_" + "f" * 32
        return protected

    def validate(protected, policy, require_all_columns):
        assert require_all_columns
        assert not protected.empty
        assert protected[FILE_ID].str.startswith("TKN_").all()

    files = [{"id": fid, "name": f"synthetic_{fid}.xlsx"} for fid in raw]
    frames, summary = collect_new_drive_frames(
        context["read"], files, manifest, KEY,
        expected_columns=["period"],
        privacy_policy={}, privacy_schema={},
        download=download, parse=parse, anonymize=anonymize, validate=validate,
    )
    return frames, summary, parse_count


def preview_with_new_batch(context, *, period=2):
    old_manifest = load_processed_manifest(context["read"])
    frames, summary, parsed = collect(context, old_manifest, new_period=period)
    staging = pd.concat(frames, ignore_index=True)
    plan = plan_incremental_load(staging, old_manifest)
    assert plan["new_files"] == 1
    new_manifest = build_file_manifest(plan["new_rows"])
    # O payload e sintetico: testa a publicacao e o hash, nao a leitura do Parquet.
    parquet = BytesIO(b"new-parquet-synthetic-payload")
    publication_plan = inc.preview_incremental_publication(
        context["read"], old_manifest, new_manifest, parquet, len(staging)
    )
    return old_manifest, summary, parsed, parquet, publication_plan


def test_e2e_one_new_file_preserves_old_batch_and_is_idempotent(context):
    old, summary, parsed, parquet, plan = preview_with_new_batch(context)
    store = context["store"]
    assert summary == {"raw_files": 2, "new_files": 1, "skipped_files": 1}
    assert len(parsed) == 1, "Apenas o Excel novo pode ser aberto"
    assert store.created == store.updated == 0, "Previa nao faz upload"

    result = inc.publish_incremental_batch(
        context["read"], context["write"], plan, parquet
    )
    assert result == {"processed_files": 2, "new_files": 1}
    assert store.created == 1
    assert store.updated == 1
    assert store.objects["PARQUET_PREVIOUS"]["content"] == context["old_parquet"]
    assert "MANIFEST_PREVIOUS" in store.objects, "ID do manifesto original deve ser mantido"
    assert len(store.objects) == 3

    current_manifest = load_processed_manifest(context["read"])
    document = json.loads(store.objects["MANIFEST_PREVIOUS"]["content"])
    assert len(document["batches"]) == 2
    assert document["batches"][0]["parquet_id"] == "PARQUET_PREVIOUS"
    assert len(current_manifest) == 2

    second_frames, second_summary, second_parsed = collect(context, current_manifest)
    assert second_frames == []
    assert second_summary == {"raw_files": 2, "new_files": 0, "skipped_files": 2}
    assert not second_parsed
    assert (store.created, store.updated) == (1, 1)


def test_e2e_manifest_update_failure_requires_reconciliation(context):
    _, _, _, parquet, plan = preview_with_new_batch(context)
    store = context["store"]
    store.fail_update = True
    with pytest.raises(RuntimeError, match="indeterminada"):
        inc.publish_incremental_batch(context["read"], context["write"], plan, parquet)
    assert store.created == 1 and store.updated == 1
    assert "PARQUET_PREVIOUS" in store.objects
    with pytest.raises(RuntimeError, match="reconcili"):
        load_processed_manifest(context["read"])
    assert len(store.objects) == 3, "Nao apagar o Parquet orfao automaticamente"


def test_e2e_duplicate_content_blocks_publication(context):
    stored = load_processed_manifest(context["read"])
    with pytest.raises(ValueError, match="pasta raw"):
        collect(context, stored, extra_duplicate=True)
    assert context["store"].created == context["store"].updated == 0


def test_e2e_replaced_period_is_blocked_before_upload(context):
    old_manifest = load_processed_manifest(context["read"])
    frames, _, _ = collect(context, old_manifest, new_period=1)
    staging = pd.concat(frames, ignore_index=True)
    with pytest.raises(ValueError, match="mensal detectada"):
        plan_incremental_load(staging, old_manifest)
    assert context["store"].created == context["store"].updated == 0
