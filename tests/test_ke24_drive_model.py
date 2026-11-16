import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.pipeline.run_ke24_drive_model import (
    prepare_temporal_matrices,
    run_model_in_memory,
)


ROOT = Path(__file__).resolve().parents[1]


def _policy():
    return json.loads(
        (ROOT / "config" / "ke24_privacy_policy.json").read_text(
            encoding="utf-8"
        )
    )


def synthetic_features():
    rows = []
    for group_index, letter in enumerate(("a", "b")):
        company = "TKN_" + letter * 32
        source_id = "TKN_" + ("c" if group_index == 0 else "d") * 32
        for index in range(30):
            held_out = index >= 24
            period = 3 if held_out else 12
            year = 2026 if held_out else 2025
            rows.append({
                "_load_id": "SYNTHETIC_TEST",
                "_source_file_sha256": source_id,
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
                "signal": float(index + group_index * 10 + 1),
                "signal_abs": float((index + 1) ** 2),
                "constant_selected": 7.0 if not held_out else 99.0,
            })
    dataset = pd.DataFrame(rows)
    manifest = pd.DataFrame([
        {"feature": col, "selected_for_model": True}
        for col in ("signal", "signal_abs", "constant_selected")
    ])
    return dataset, manifest


def test_treino_temporal_inferencia_real_em_memoria():
    dataset, manifest = synthetic_features()
    result = run_model_in_memory(dataset, manifest, _policy())

    assert len(result["train_context"]) == 48
    assert len(result["validation_context"]) == 12
    assert result["removed_constants"] == 1
    assert list(result["train_matrix"].columns) == ["signal", "signal_abs"]
    assert list(result["validation_matrix"].columns) == ["signal", "signal_abs"]
    assert len(result["scores"]) == 12
    assert result["group_count"] == 2
    assert np.isfinite(result["scores"]["anomaly_score"]).all()
    assert result["reference_flags"] >= 0


def test_holdout_nao_interfere_na_remocao_de_constantes():
    dataset, manifest = synthetic_features()
    original = prepare_temporal_matrices(dataset, manifest, _policy())
    last_period = dataset["calendar_year"].eq(2026)
    dataset.loc[last_period, "constant_selected"] = -99999.0
    modified = prepare_temporal_matrices(dataset, manifest, _policy())

    assert original["removed_constants"] == modified["removed_constants"] == 1
    pd.testing.assert_frame_equal(
        original["train_matrix"], modified["train_matrix"]
    )


def test_infinito_no_holdout_e_bloqueado():
    dataset, manifest = synthetic_features()
    dataset.loc[dataset["calendar_year"].eq(2026), "signal"] = float("inf")
    with pytest.raises(ValueError, match="infinitos"):
        prepare_temporal_matrices(dataset, manifest, _policy())


def test_sem_variacao_no_treino_e_bloqueado():
    dataset, manifest = synthetic_features()
    train = dataset["calendar_year"].eq(2025)
    dataset.loc[train, ["signal", "signal_abs"]] = 10.0
    with pytest.raises(ValueError, match="Todas as features são constantes"):
        prepare_temporal_matrices(dataset, manifest, _policy())
