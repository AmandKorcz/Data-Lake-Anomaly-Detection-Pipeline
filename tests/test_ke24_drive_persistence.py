from io import BytesIO

import pandas as pd
import pytest

from src.loading.ke24_drive_manifest import (
    load_processed_manifest,
    list_processed_files,
)
from src.loading import ke24_drive_publish as publish


class FakeRequest:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class FakeDriveFiles:
    def __init__(self, state):
        self.state = state

    def list(self, **kwargs):
        def call():
            return {
                "files": [
                    {"id": key, "name": value["name"], "mimeType": value["mimeType"]}
                    for key, value in self.state["objects"].items()
                ],
            }
        return FakeRequest(call)

    def create(self, body, media_body, fields):
        def call():
            if body["name"] == "ke24_ingestion_manifest.json" and self.state["fail_manifest"]:
                raise ConnectionError("Simulação de falha durante o upload do manifesto")
            key = str(len(self.state["objects"]) + 1)
            media_body.stream.seek(0)
            content = media_body.stream.read()
            self.state["objects"][key] = {
                "name": body["name"],
                "mimeType": body["mimeType"],
                "content": content,
            }
            return {"id": key, "name": body["name"], "parents": body["parents"]}
        return FakeRequest(call)

    def get_media(self, fileId):
        return FakeRequest(lambda: self.state["objects"][fileId]["content"])


class FakeDrive:
    def __init__(self):
        self.state = {"objects": {}, "fail_manifest": False}

    def files(self):
        return FakeDriveFiles(self.state)


class FakeMediaUpload:
    def __init__(self, stream, mimetype, resumable):
        self.stream = stream


def make_manifest():
    return pd.DataFrame([
        {
            "_source_file_sha256": "TKN_" + "a" * 32,
            "company_code": "TKN_" + "1" * 32,
            "period": 3,
            "period_year": "03.2026",
            "currency": "USD",
            "record_count": 1,
        },
        {
            "_source_file_sha256": "TKN_" + "b" * 32,
            "company_code": "TKN_" + "2" * 32,
            "period": 3,
            "period_year": "03.2026",
            "currency": "USD",
            "record_count": 2,
        },
    ])


@pytest.fixture
def setup_env(monkeypatch):
    monkeypatch.setenv("KE24_DRIVE_PROCESSED_FOLDER_ID", "TEST_FOLDER_123")
    monkeypatch.setattr(publish, "MediaIoBaseUpload", FakeMediaUpload)
    return FakeDrive(), make_manifest(), BytesIO(b"PARQUET_SINTETICO")


def test_primeira_publicacao_manifesto_e_reexecucao(setup_env):
    service, manifest, parquet = setup_env
    plan = publish.preview_first_publication(service, manifest, parquet)
    result = publish.publish_initial_batch(
        read_service=service,
        write_service=service,
        planned_batch=plan,
        parquet_buffer=parquet,
        updated_manifest=manifest,
        processed_row_count=3,
    )
    assert result["parquet_id"] != result["manifest_id"]
    assert len(list_processed_files(service)) == 2
    loaded = load_processed_manifest(service)
    assert len(loaded) == 2
    assert set(loaded["_source_file_sha256"]) == set(manifest["_source_file_sha256"])
    with pytest.raises(RuntimeError, match="não vazia"):
        publish.preview_first_publication(service, manifest, parquet)


def test_falha_no_manifesto_bloqueia_reexecucao(setup_env):
    service, manifest, parquet = setup_env
    plan = publish.preview_first_publication(service, manifest, parquet)
    service.state["fail_manifest"] = True
    with pytest.raises(RuntimeError, match="incompleta ou indeterminada"):
        publish.publish_initial_batch(
            service, service, plan, parquet, manifest, 3
        )
    assert len(list_processed_files(service)) == 1
    with pytest.raises(RuntimeError, match="reconciliação"):
        load_processed_manifest(service)


def test_corrupcao_do_parquet_bloqueia_manifesto(setup_env):
    service, manifest, parquet = setup_env
    plan = publish.preview_first_publication(service, manifest, parquet)
    result = publish.publish_initial_batch(service, service, plan, parquet, manifest, 3)
    service.state["objects"][result["parquet_id"]]["content"] = b"ALTERADO"
    with pytest.raises(RuntimeError, match="Integridade"):
        load_processed_manifest(service)


def test_manifesto_sem_parquet_bloqueia(setup_env):
    service, manifest, parquet = setup_env
    plan = publish.preview_first_publication(service, manifest, parquet)
    result = publish.publish_initial_batch(service, service, plan, parquet, manifest, 3)
    del service.state["objects"][result["parquet_id"]]
    with pytest.raises(RuntimeError, match="ausentes"):
        load_processed_manifest(service)
