import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.features import build_ke24_ml_features as features
from src.ml import build_ke24_alert_candidates as candidates
from src.ml import build_ke24_business_validation_dataset as business
from src.ml import classify_ke24_alerts as classification
from src.ml import evaluate_ke24_isolation_forest_stability as stability
from src.ml import prepare_ke24_training_matrix as training
from src.ml import profile_ke24_anomaly_materiality as materiality
from src.ml import train_ke24_isolation_forest as detection
from src.ml import validate_ke24_business_validation as review
from src.ml.protect_ke24_alert_rules import protect_company_rule_keys
from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
from src.processing import prepare_ke24_processed as processing


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"

def load_configuration(filename):
    with (PROJECT_ROOT / "config" / filename).open(
        "r", encoding="utf-8"
    ) as file:
        return json.load(file)


def create_synthetic_ke24(schema):
    columns = schema["columns"]
    start = columns.index("sales_quantity")
    dimensions = columns[:start]
    measures = columns[start:]

    records = []

    for index in range(40):
        company = "COMP_A" if index < 20 else "COMP_B"
        period = index % 12 + 1

        # Metadados primeiro, conforme a estrutura da ingestão
        record = {
            "_load_id": "LOAD_TEST_01",
            "_source_file_sha256": "f" * 64,
            "_source_system": "SAP_KE24",
            "_source_file": "synthetic_ke24.xlsx",
            "_source_row_number": index + 2,
            "_ingested_at_utc": pd.Timestamp(
                "2026-01-01T00:00:00Z"
            ),
        }

        record.update({
            column: "EXEMPLO" for column in dimensions
        })

        record.update({
            column: 0.0 for column in measures
        })

        record.update({
            "company_code": company,
            "currency_type": 10,
            "currency": "BRL",
            "period": period,
            "period_year": f"{period:02d}.2026",
            "profit_center": f"CENTRO_{company}",
            "product": f"PROD_{index % 3}",
            "reference_document": f"DOC_{index:03d}",
            "sales_quantity": float(index % 3 + 1),
            "revenue": (
                200000.0 if index == 39
                else float(500 + (index % 13) * 29)
            ),
            "c_4rm_copper_wire": -float(40 + index % 7 * 5),
        })

        records.append(record)

    return pd.DataFrame(records)


def build_test_rules():
    return {
        "status": "experimental_auto_classification",
        "currency_basis": {"currency_type": 10},
        "business_thresholds_by_company": {
            company: {
                "currency": "BRL",
                "materiality_medium_min": 100.0,
                "materiality_high_min": 300.0,
            }
            for company in ("COMP_A", "COMP_B")
        },
        "technical_thresholds": {
            "anomaly_percentile_medium_min": 90,
            "anomaly_percentile_high_min": 95,
            "stability_medium_min": 40,
            "stability_high_min": 100,
        },
    }


class TestKE24MLPipelineIntegration(unittest.TestCase):

    def test_protected_pipeline_to_alert_classification(self):
        schema = load_configuration("ke24_schema.json")
        policy = load_configuration("ke24_privacy_policy.json")

        raw = create_synthetic_ke24(schema)

        protected = anonymize_ke24_dataframe(
            raw, policy, schema, TEST_KEY
        )

        self.assertEqual(len(raw), len(protected))

        # Valores financeiros permanecem inalterados
        measure_start = schema["columns"].index("sales_quantity")
        measure_columns = schema["columns"][measure_start:]

        pd.testing.assert_frame_equal(
            raw[measure_columns],
            protected[measure_columns],
            check_exact=True,
        )

        # Identificadores protegidos mantêm o formato HMAC
        for column in (
            "company_code",
            "profit_center",
            "reference_document",
            "_source_file",
        ):
            self.assertTrue(
                protected[column]
                .astype("string")
                .str.fullmatch(r"TKN_[0-9a-f]{32}")
                .all()
            )

        # Processed
        processing.validate_input_data(protected)

        processed, _ = processing.standardize_measure_types(
            protected.copy()
        )
        processed = processing.add_period_key(processed)
        processed = processing.sort_processed_data(processed)

        processing.validate_processed_data(
            processed, len(protected)
        )

        # Feature engineering
        selected_measures = features.get_measure_columns(processed)
        measure_matrix = features.prepare_measure_matrix(
            processed, selected_measures
        )
        active, _ = features.select_active_measures(measure_matrix)
        engineered = features.build_engineered_features(measure_matrix)

        ml_features = pd.concat([
            processed[features.CONTEXT_COLUMNS].reset_index(drop=True),
            measure_matrix[active].reset_index(drop=True),
            engineered.reset_index(drop=True),
        ], axis=1)

        features.validate_output(
            processed, ml_features, list(engineered.columns)
        )

        manifest = features.build_feature_manifest(
            selected_measures, active, list(engineered.columns)
        )

        # Matriz de treinamento
        selected = training.get_selected_features(manifest)
        matrix = training.build_training_matrix(
            ml_features, selected
        )

        matrix = matrix.loc[
            :, matrix.nunique(dropna=False).gt(1)
        ]

        self.assertGreater(len(matrix.columns), 0)

        # Detecção com Isolation Forest
        detection.validate_inputs(matrix, ml_features)
        model = detection.train_isolation_forest(matrix)
        scores = detection.build_anomaly_scores(model, matrix)
        anomaly_results = detection.build_results(
            ml_features, scores
        )

        self.assertEqual(len(anomaly_results), len(raw))
        self.assertTrue(anomaly_results["anomaly_score"].notna().all())

        # Materialidade financeira
        financial = materiality.get_financial_features(manifest)
        materiality.validate_inputs(
            ml_features, anomaly_results, financial
        )
        materiality_result = materiality.build_materiality_dataset(
            ml_features, anomaly_results, financial
        )

        # Avaliação real de estabilidade, isolada em arquivos temporários
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            training_file = directory / "matrix.parquet"
            context_file = directory / "features.parquet"
            stability_file = directory / "stability.csv"

            matrix.to_parquet(training_file, index=False)
            ml_features.to_parquet(context_file, index=False)

            with (
                patch.object(stability, "TRAINING_FILE", training_file),
                patch.object(stability, "CONTEXT_FILE", context_file),
                patch.object(stability, "OUTPUT_FILE", stability_file),
                redirect_stdout(io.StringIO()),
            ):
                stability.main()

            stability_result = pd.read_csv(stability_file)

        # Seleção de candidatos
        candidates.validate_inputs(
            materiality_result, stability_result
        )
        selected_candidates = candidates.build_alert_candidates(
            materiality_result, stability_result
        )

        self.assertFalse(selected_candidates.empty)

        # Dataset de validação de negócio
        business.validate_inputs(
            selected_candidates, ml_features
        )

        with patch.object(
            business, "load_existing_validation", return_value=None
        ):
            validation_data = business.build_validation_dataset(
                selected_candidates, ml_features
            )

        review.validate_structure(validation_data)
        review.validate_review_status(validation_data)
        review.validate_pending_records(validation_data)

        # Regras exclusivamente fictícias, protegidas em memória
        protected_rules = protect_company_rule_keys(
            build_test_rules(), TEST_KEY
        )

        classification.validate_dataset(validation_data)
        classification.validate_currency_basis(
            validation_data, protected_rules
        )

        result = classification.build_classification(
            validation_data, protected_rules
        )

        # Confere os resultados finais
        self.assertEqual(len(result), len(selected_candidates))

        self.assertTrue(
            result["alert_level"].isin(
                {"LOW", "MEDIUM", "HIGH"}
            ).all()
        )

        self.assertTrue(
            result["classification_status"]
            .eq("EXPERIMENTAL_CLASSIFICATION")
            .all()
        )

        self.assertTrue(
            result["company_code"]
            .astype("string")
            .str.fullmatch(r"TKN_[0-9a-f]{32}")
            .all()
        )


if __name__ == "__main__":
    unittest.main()
