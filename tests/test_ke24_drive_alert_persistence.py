import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.alerts.build_ke24_pretrained_outputs import build_pretrained_outputs
from src.loading import ke24_drive_alert_history as history_store

ROOT = Path(__file__).resolve().parents[1]


def token(character):
    return "TKN_" + character * 32


def privacy_policy():
    return json.loads((ROOT / "config" / "ke24_privacy_policy.json").read_text(encoding="utf-8"))


def make_history(row_numbers=(2,)):
    rows = []
    for n in row_numbers:
        rows.append({
            "_source_file": token("a"),
            "_source_file_sha256": token("b"),
            "_source_row_number": n,
            "company_code": token("c"),
            "reference_document": token("d"),
            "period_key": "2026-03",
            "profit_center": token("e"),
            "product": token("f"),
            "currency": "BRL", "currency_type": 10,
            "alert_level": "PENDING",
            "alert_reason": "classification_rules_not_approved",
            "classification_status": "PENDING_BUSINESS_RULE",
            "anomaly_score": 0.62,
            "anomaly_rank": n,
            "anomaly_flag": True,
            "training_p95_threshold": 0.55,
            "score_margin": 0.07,
            "reference_p95_flag": True,
            "model_version": "test_temporal_s42",
            "signal_method": "training_p95",
            "total_absolute_value": 500.0,
            "materiality_percentile": 100.0,
            "stability_rate": 80.0,
            "dominant_measure": "revenue",
            "dominant_measure_value": 500.0,
        })
    classified = pd.DataFrame(rows)
    records, _ = build_pretrained_outputs(
        classified,
        None,
        datetime(2026, 3, 10, tzinfo=timezone.utc),
        privacy_policy(),
    )
    return records


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    def __init__(self):
        self.files_by_id = {}
        self.counter = 0

    def files(self):
        return self

    def list(self, *, q, fields, pageSize, pageToken=None):
        import re
        match = re.search(r"'([^']+)' in parents", q)
        folder = match.group(1)
        items = [
            {"id": key, "name": value["name"], "mimeType": value["mimeType"]}
            for key, value in self.files_by_id.items() if value["folder"] == folder
        ]
        return FakeRequest(lambda: {"files": items, "nextPageToken": None})

    def get_media(self, *, fileId):
        return FakeRequest(lambda: self.files_by_id[fileId]["content"])

    def create(self, *, body, media_body, fields):
        def run():
            self.counter += 1
            file_id = f"file_{self.counter}"
            self.files_by_id[file_id] = {
                "name": body["name"],
                "folder": body["parents"][0],
                "mimeType": body["mimeType"],
                "content": media_body.getbytes(0, media_body.size()),
            }
            return {"id": file_id, "name": body["name"], "parents": body["parents"]}
        return FakeRequest(run)

    def update(self, *, fileId, media_body, fields):
        def run():
            record = self.files_by_id[fileId]
            record["content"] = media_body.getbytes(0, media_body.size())
            return {"id": fileId, "name": record["name"], "parents": [record["folder"]]}
        return FakeRequest(run)


@pytest.fixture(autouse=True)
def folder(monkeypatch):
    monkeypatch.setenv("KE24_DRIVE_ALERTS_FOLDER_ID", "test_alerts_folder")
    monkeypatch.setenv("KE24_DRIVE_PROCESSED_FOLDER_ID", "test_processed_folder")
    monkeypatch.setenv("KE24_DRIVE_RAW_FOLDER_ID", "test_raw_folder")


def test_initial_publication_is_verified_and_reloadable():
    service = FakeDrive()
    state = history_store.load_alert_history_state(service, privacy_policy())
    current = make_history()
    merged, new = history_store.append_new_alerts(state.history, current, privacy_policy())
    assert new == 1
    result = history_store.publish_alert_history(service, service, state, merged, privacy_policy())
    assert result["published"] is True
    reloaded = history_store.load_alert_history_state(service, privacy_policy())
    assert len(reloaded.history) == 1
    assert reloaded.history["alert_id"].tolist() == current["alert_id"].tolist()
    assert len(reloaded.snapshots) == 1


def test_reexecution_keeps_first_detection_and_no_extra_upload():
    service = FakeDrive()
    state = history_store.load_alert_history_state(service, privacy_policy())
    history_store.publish_alert_history(service, service, state, make_history(), privacy_policy())
    before = history_store.load_alert_history_state(service, privacy_policy())
    current = make_history()
    merged, count = history_store.append_new_alerts(before.history, current, privacy_policy())
    assert count == 0
    assert merged["detected_at_utc"].iloc[0] == before.history["detected_at_utc"].iloc[0]
    result = history_store.publish_alert_history(service, service, before, merged, privacy_policy())
    assert result["published"] is False
    assert service.counter == 2


def test_new_candidate_appended_without_overwriting_previous():
    service = FakeDrive()
    first = history_store.load_alert_history_state(service, privacy_policy())
    history_store.publish_alert_history(service, service, first, make_history((2,)), privacy_policy())
    previous = history_store.load_alert_history_state(service, privacy_policy())
    candidate = make_history((2, 3))
    merged, count = history_store.append_new_alerts(previous.history, candidate, privacy_policy())
    assert count == 1 and len(merged) == 2
    result = history_store.publish_alert_history(service, service, previous, merged, privacy_policy())
    assert result["published"] is True
    remote = history_store.load_alert_history_state(service, privacy_policy())
    assert len(remote.history) == 2
    assert len(remote.snapshots) == 2
    assert remote.history["alert_id"].is_unique


def test_prior_manual_investigation_is_not_reset():
    previous = make_history((2,))
    previous.loc[0, "investigation_status"] = "INVESTIGATING"
    previous.loc[0, "classification_status"] = "READY_FOR_FINAL_CLASSIFICATION"
    previous.loc[0, "alert_level"] = "HIGH"
    current = make_history((2, 3))
    result, new = history_store.append_new_alerts(previous, current, privacy_policy())
    original = result.loc[result["alert_id"].eq(previous.loc[0, "alert_id"])].iloc[0]
    assert new == 1
    assert original["investigation_status"] == "INVESTIGATING"
    assert original["classification_status"] == "READY_FOR_FINAL_CLASSIFICATION"
    assert original["alert_level"] == "HIGH"


def test_corrupted_snapshot_blocks_read():
    service = FakeDrive()
    first = history_store.load_alert_history_state(service, privacy_policy())
    history_store.publish_alert_history(service, service, first, make_history(), privacy_policy())
    record = next(v for v in service.files_by_id.values() if v["name"].endswith(".csv"))
    record["content"] += b"TAMPERED"
    with pytest.raises(RuntimeError, match="SHA-256"):
        history_store.load_alert_history_state(service, privacy_policy())


def test_orphan_file_blocks_read_and_publication():
    service = FakeDrive()
    service.files_by_id["orphan"] = {
        "name": "unregistered.csv", "folder": "test_alerts_folder",
        "mimeType": "text/csv", "content": b"secret",
    }
    with pytest.raises(RuntimeError, match="manifesto unico"):
        history_store.load_alert_history_state(service, privacy_policy())


def test_alerts_folder_must_differ_from_processed(monkeypatch):
    monkeypatch.setenv("KE24_DRIVE_ALERTS_FOLDER_ID", "test_processed_folder")
    with pytest.raises(ValueError, match="diferente"):
        history_store.get_alerts_folder_id()


def test_historical_alert_cannot_be_removed():
    service = FakeDrive()
    first = history_store.load_alert_history_state(service, privacy_policy())
    history_store.publish_alert_history(service, service, first, make_history((2, 3)), privacy_policy())
    state = history_store.load_alert_history_state(service, privacy_policy())
    with pytest.raises(RuntimeError, match="remover"):
        history_store.publish_alert_history(service, service, state, make_history((2,)), privacy_policy())
