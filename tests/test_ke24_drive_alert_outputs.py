import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.pipeline.run_ke24_drive_alert_outputs import (
    PENDING_CLASSIFICATION_STATUS,
    build_pending_alert_outputs_in_memory,
)


ROOT = Path(__file__).resolve().parents[1]


def token(letter):
    return "TKN_" + letter * 32


def policy():
    return json.loads(
        (ROOT / "config" / "ke24_privacy_policy.json").read_text(
            encoding="utf-8"
        )
    )


def make_candidates():
    return pd.DataFrame({
        "_source_file": [token("a")],
        "_source_file_sha256": [token("b")],
        "_source_row_number": [2],
        "company_code": [token("c")],
        "reference_document": [token("d")],
        "period_key": ["2026-03"],
        "profit_center": [token("e")],
        "product": [token("f")],
        "currency": ["BRL"],
        "currency_type": [10],
        "anomaly_score": [0.62],
        "anomaly_rank": [1],
        "anomaly_flag": [True],
        "training_p95_threshold": [0.55],
        "score_margin": [0.07],
        "reference_p95_flag": [True],
        "model_version": ["ke24_drive_preview_s42"],
        "signal_method": ["training_p95"],
        "total_absolute_value": [500.0],
        "materiality_percentile": [100.0],
        "stability_rate": [100.0],
        "dominant_measure": ["revenue"],
        "dominant_measure_value": [500.0],
    })


def run(candidates=None, previous_history=None, when=None):
    candidates = make_candidates() if candidates is None else candidates
    when = when or datetime(2026, 4, 5, tzinfo=timezone.utc)
    return build_pending_alert_outputs_in_memory(
        candidates,
        policy(),
        when,
        previous_history=previous_history,
    )


def test_pending_outputs_do_not_fake_classification_and_preserve_input():
    candidates = make_candidates()
    original = candidates.copy(deep=True)

    result = run(candidates)
    history = result["history"]
    dashboard = result["dashboard"]

    pd.testing.assert_frame_equal(candidates, original)
    assert len(history) == len(dashboard) == 1
    assert result["pending_current"] == 1
    assert history.loc[0, "alert_level"] == "PENDING"
    assert history.loc[0, "classification_status"] == PENDING_CLASSIFICATION_STATUS
    assert history.loc[0, "investigation_status"] == "DETECTED"
    assert history.loc[0, "alert_id"].startswith("ALT_")
    assert dashboard.loc[0, "alert_count"] == 1
    assert "anomaly_score_percentile" not in dashboard.columns


def test_rerun_preserves_alert_id_first_detection_and_investigation():
    first = run()
    previous = first["history"].copy()
    previous.loc[0, "investigation_status"] = "INVESTIGATING"

    second = run(
        previous_history=previous,
        when=datetime(2026, 4, 6, tzinfo=timezone.utc),
    )
    current = second["history"]

    assert len(current) == 1
    assert current.loc[0, "alert_id"] == previous.loc[0, "alert_id"]
    assert current.loc[0, "detected_at_utc"] == previous.loc[0, "detected_at_utc"]
    assert current.loc[0, "investigation_status"] == "INVESTIGATING"


def test_unprotected_reference_document_is_blocked():
    bad = make_candidates()
    bad.loc[0, "reference_document"] = "REFERENCE_REAL"
    with pytest.raises(ValueError, match="documentos de referência não protegidos"):
        run(bad)


def test_inconsistent_p95_candidate_is_blocked():
    bad = make_candidates()
    bad.loc[0, "reference_p95_flag"] = False
    with pytest.raises(ValueError, match="Inconsistent training P95 flags"):
        run(bad)


def test_already_classified_input_is_blocked():
    bad = make_candidates()
    bad["alert_level"] = "HIGH"
    with pytest.raises(ValueError, match="já contém campos de classificação"):
        run(bad)


def test_empty_candidate_batch_creates_empty_preview():
    result = run(make_candidates().iloc[:0].copy())
    assert result["history"].empty
    assert result["dashboard"].empty
    assert "alert_count" in result["dashboard"].columns
