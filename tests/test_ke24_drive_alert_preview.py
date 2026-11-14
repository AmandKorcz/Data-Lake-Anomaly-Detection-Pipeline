import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.pipeline.run_ke24_drive_alert_preview import (
    build_alert_preview_in_memory,
)
from src.ml.evaluate_ke24_isolation_forest_stability import RANDOM_STATES
from src.ml.ke24_record_identity import IDENTITY_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _privacy_policy():
    return json.loads(
        (PROJECT_ROOT / "config" / "ke24_privacy_policy.json")
        .read_text(encoding="utf-8")
    )


def _synthetic_case():
    records = []
    for group_index, letter in enumerate(("a", "b")):
        company = "TKN_" + letter * 32
        source = "TKN_" + ("c" if group_index == 0 else "d") * 32
        for index in range(30):
            held_out = index >= 24
            period = 3 if held_out else 12
            year = 2026 if held_out else 2025
            value = float(index + 1 + group_index * 10)
            records.append({
                "_load_id": "SYNTHETIC_TEST",
                "_source_file_sha256": source,
                "_source_file": "TKN_" + "e" * 32,
                "_source_system": "SAP_KE24",
                "_source_row_number": index + 2,
                "_ingested_at_utc": pd.Timestamp("2026-04-01", tz="UTC"),
                "company_code": company,
                "currency": "USD",
                "currency_type": 10,
                "period": period,
                "period_year": f"{period:02d}.{year}",
                "calendar_year": year,
                "period_key": f"{year}-{period:02d}",
                "period_date": pd.Timestamp(year, period, 1),
                "profit_center": "TKN_" + "f" * 32,
                "division": "TKN_" + "1" * 32,
                "product": "TKN_" + "2" * 32,
                "record_type": "NORMAL",
                "reference_document": "TKN_" + "3" * 32,
                "revenue": value,
                "scaled_revenue": value ** 2,
                "total_signed_value": value,
                "total_absolute_value": value,
                "max_absolute_value": value,
            })
    dataset = pd.DataFrame(records)
    feature_manifest = pd.DataFrame([
        {"feature": "revenue", "feature_type": "financial_measure", "selected_for_model": True},
        {"feature": "scaled_revenue", "feature_type": "engineered", "selected_for_model": True},
    ])
    return dataset, feature_manifest


def test_preview_usa_p95_treino_e_cinco_seeds():
    dataset, manifest = _synthetic_case()
    result = build_alert_preview_in_memory(dataset, manifest, _privacy_policy())

    assert result["training_rows"] == 48
    assert result["validation_rows"] == 12
    assert result["group_count"] == 2
    assert result["seeds"] == len(RANDOM_STATES) == 5
    assert result["candidate_count"] == result["primary_flags"]
    assert result["classified"] is None
    assert result["stable_all_seeds"] <= result["candidate_count"]
    assert result["materiality"]["materiality_percentile"].between(0, 100).all()
    assert result["candidates"]["reference_p95_flag"].all()
    assert (result["candidates"]["score_margin"] >= 0).all()
    assert not result["candidates"].duplicated(IDENTITY_COLUMNS).any()
    assert np.isfinite(result["materiality"]["total_absolute_value"].to_numpy(dtype=float)).all()


def test_regras_invalidas_sao_bloqueadas_antes_do_treino():
    dataset, manifest = _synthetic_case()
    with pytest.raises(ValueError, match="regras protegidas"):
        build_alert_preview_in_memory(
            dataset, manifest, _privacy_policy(), protected_rules="INVALIDO"
        )
