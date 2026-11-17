"""Testes offline para atualizacao segura do manifesto do Google Drive."""
import hashlib
import io
import json

import pandas as pd
import pytest

from src.loading import ke24_drive_incremental_publish as inc


def record(token, period, count=1):
    return {
        "_source_file_sha256": "TKN_" + token * 32,
        "company_code": "TKN_" + "c" * 32,
        "period": period,
        "period_year": f"{period:02d}.2026",
        "currency": "USD",
        "record_count": count,
    }


class Request:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class Store:
    def __init__(self):
        self.objects = {}
        self.next_id = 20
        self.fail_update = False
        self.can_edit = True
        self.trashed = False
        self.update_calls = 0
        self.create_calls = 0


class Files:
    def __init__(self, store):
        self.store = store

    def list(self, **kwargs):
        return Request(lambda: {"files": [
            {"id": key, "name": obj["name"], "mimeType": obj["mimeType"]}
            for key, obj in self.store.objects.items()
        ]})

    def get_media(self, fileId):
        return Request(lambda: self.store.objects[fileId]["content"])

    def get(self, fileId, fields):
        if fileId == "FOLDER_TEST":
            return Request(lambda: {
                "id": fileId,
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": self.store.trashed,
            })
        return Request(lambda: {
            "id": fileId, "trashed": False,
            "capabilities": {"canEdit": self.store.can_edit},
        })

    def create(self, body, media_body, fields):
        def cb():
            self.store.create_calls += 1
            self.store.next_id += 1
            file_id = str(self.store.next_id)
            media_body.stream.seek(0)
            self.store.objects[file_id] = {
                "name": body["name"],
                "mimeType": body["mimeType"],
                "content": media_body.stream.read(),
            }
            return {"id": file_id, "name": body["name"], "parents": body["parents"]}
        return Request(cb)

    def update(self, fileId, media_body, fields):
        def cb():
            self.store.update_calls += 1
            if self.store.fail_update:
                raise ConnectionError("simulated update failure")
            media_body.stream.seek(0)
            self.store.objects[fileId]["content"] = media_body.stream.read()
            return {"id": fileId, "name": self.store.objects[fileId]["name"]}
        return Request(cb)


class Drive:
    def __init__(self, store):
        self.store = store

    def files(self):
        return Files(self.store)


class FakeMedia:
    def __init__(self, stream, mimetype, resumable):
        self.stream = stream


def mock_serializer(frame, batches):
    """Verifica somente o contrato usado pelo teste; o usuario testa o real."""
    records = frame[inc.MANIFEST_COLUMNS].to_dict("records")
    hashes = [row[inc.FILE_ID] for row in records]
    if len(set(hashes)) != len(hashes):
        raise ValueError("duplicated file")
    periods = [tuple(row[c] for c in inc.BUSINESS_KEY) for row in records]
    if len(set(periods)) != len(periods):
        raise ValueError("duplicate business period")
    batch_ids = [batch["batch_id"] for batch in batches]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("batch collision")
    registered = set()
    for batch in batches:
        assert all(key in batch for key in (
            "batch_id", "parquet_id", "parquet_name", "parquet_sha256",
            "byte_count", "record_count", "source_file_ids"
        ))
        ids = batch["source_file_ids"]
        assert not registered.intersection(ids)
        registered.update(ids)
        assert sum(row["record_count"] for row in records if row[inc.FILE_ID] in ids) == batch["record_count"]
    if registered != set(hashes):
        raise ValueError("unreferenced sources")
    return json.dumps({"schema_version": 2, "files": records, "batches": batches}).encode()


def mock_validate(document):
    frame = pd.DataFrame(document["files"])
    mock_serializer(frame, document["batches"])
    return frame


def mock_manifest_loader(service):
    store = service.store
    if set(store.objects) != {"M1", *[b["parquet_id"] for b in json.loads(store.objects["M1"]["content"])["batches"]]}:
        raise RuntimeError("orphan files")
    doc = json.loads(store.objects["M1"]["content"])
    for batch in doc["batches"]:
        raw = store.objects[batch["parquet_id"]]["content"]
        if hashlib.sha256(raw).hexdigest() != batch["parquet_sha256"]:
            raise RuntimeError("parquet integrity")
    return mock_validate(doc)


def mock_upload(service, folder_id, name, content, mime_type):
    result = service.files().create(
        body={"name": name, "parents": [folder_id], "mimeType": mime_type},
        media_body=FakeMedia(io.BytesIO(content), mime_type, False), fields="id,name,parents",
    ).execute()
    assert service.files().get_media(fileId=result["id"]).execute() == content
    return result["id"]


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setattr(inc, "get_processed_folder_id", lambda: "FOLDER_TEST")
    monkeypatch.setattr(inc, "list_processed_files", lambda service: service.files().list().execute()["files"])
    monkeypatch.setattr(inc, "load_processed_manifest", mock_manifest_loader)
    monkeypatch.setattr(inc, "validate_manifest_document", mock_validate)
    monkeypatch.setattr(inc, "serialize_processed_manifest", mock_serializer)
    monkeypatch.setattr(inc, "_upload_and_verify", mock_upload)
    monkeypatch.setattr(inc, "MediaIoBaseUpload", FakeMedia)
    monkeypatch.setattr(inc, "LOCK_PATH", tmp_path / "ke24.lock")

    store = Store()
    old = record("a", 1, count=2)
    old_batch = {
        "batch_id": "1" * 16, "parquet_id": "P1",
        "parquet_name": "ke24_processed_" + "1" * 16 + ".parquet",
        "parquet_sha256": hashlib.sha256(b"old-protected-parquet").hexdigest(),
        "byte_count": len(b"old-protected-parquet"), "record_count": 2,
        "source_file_ids": [old[inc.FILE_ID]],
    }
    store.objects = {
        "P1": {"name": old_batch["parquet_name"], "mimeType": "application/octet-stream", "content": b"old-protected-parquet"},
        "M1": {"name": inc.MANIFEST_NAME, "mimeType": "application/json", "content": mock_serializer(pd.DataFrame([old]), [old_batch])},
    }
    read = Drive(store)
    write = Drive(store)
    old_frame = pd.DataFrame([old])
    new_frame = pd.DataFrame([record("b", 2, count=3)])
    parquet = io.BytesIO(b"new-protected-parquet")
    return store, read, write, old_frame, new_frame, parquet


def plan_for(ctx):
    _, read, _, old, new, parquet = ctx
    return inc.preview_incremental_publication(read, old, new, parquet, 3)


def test_preview_preserves_existing_batches_without_writing(ctx):
    store, _, _, old, new, _ = ctx
    planned = plan_for(ctx)
    assert len(planned["base"].batches) == 1
    assert len(planned["manifest"]) == 2
    assert store.create_calls == 0 and store.update_calls == 0


def test_publish_adds_parquet_and_updates_same_manifest_id(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    result = inc.publish_incremental_batch(read, write, planned, parquet)
    assert result == {"processed_files": 2, "new_files": 1}
    assert store.create_calls == 1 and store.update_calls == 1
    assert "M1" in store.objects and "P1" in store.objects
    doc = json.loads(store.objects["M1"]["content"])
    assert len(doc["batches"]) == 2
    assert doc["batches"][0]["parquet_id"] == "P1"
    assert len(store.objects) == 3


def test_refuses_stale_manifest_before_any_write(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    store.objects["M1"]["content"] += b"\n"
    with pytest.raises(RuntimeError, match="Estado remoto mudou"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 0 and store.update_calls == 0


def test_refuses_write_scope_without_manifest_edit_access(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    store.can_edit = False
    with pytest.raises(RuntimeError, match="nao pode editar"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 0 and store.update_calls == 0


def test_partial_update_failure_requires_manual_reconciliation(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    store.fail_update = True
    with pytest.raises(RuntimeError, match="indeterminada"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 1 and store.update_calls == 1
    assert len(store.objects) == 3
    assert json.loads(store.objects["M1"]["content"])["batches"][0]["parquet_id"] == "P1"
    with pytest.raises(RuntimeError, match="orphan"):
        mock_manifest_loader(read)


def test_parquet_changed_after_preview_blocks_write(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    parquet.seek(0)
    parquet.write(b"tampered")
    with pytest.raises(RuntimeError, match="mudou apos"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 0


def test_cannot_replace_existing_business_period(ctx):
    store, read, write, old, new, parquet = ctx
    new.loc[0, "period"] = 1
    new.loc[0, "period_year"] = "01.2026"
    with pytest.raises(ValueError, match="duplicate business period"):
        inc.preview_incremental_publication(read, old, new, parquet, 3)
    assert store.create_calls == 0


def test_cannot_repeat_source_token(ctx):
    store, read, write, old, new, parquet = ctx
    new.loc[0, inc.FILE_ID] = old.loc[0, inc.FILE_ID]
    with pytest.raises(ValueError, match="duplicated file"):
        inc.preview_incremental_publication(read, old, new, parquet, 3)


def test_cannot_publish_wrong_processed_row_count(ctx):
    store, read, write, old, new, parquet = ctx
    with pytest.raises(ValueError, match="Total processed"):
        inc.preview_incremental_publication(read, old, new, parquet, 2)


def test_cannot_operate_if_processed_folder_trashed(ctx):
    store, read, write, old, new, parquet = ctx
    store.trashed = True
    with pytest.raises(RuntimeError, match="na lixeira"):
        inc.preview_incremental_publication(read, old, new, parquet, 3)


def test_locked_publication_is_blocked(ctx):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    inc.LOCK_PATH.touch()
    with pytest.raises(RuntimeError, match="lock pendente"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 0


def test_manifest_changed_after_parquet_upload_blocks_update(ctx, monkeypatch):
    store, read, write, old, new, parquet = ctx
    planned = plan_for(ctx)
    def mutating_upload(*args):
        file_id = mock_upload(*args)
        store.objects["M1"]["content"] += b"\n"
        return file_id
    monkeypatch.setattr(inc, "_upload_and_verify", mutating_upload)
    with pytest.raises(RuntimeError, match="indeterminada"):
        inc.publish_incremental_batch(read, write, planned, parquet)
    assert store.create_calls == 1 and store.update_calls == 0
