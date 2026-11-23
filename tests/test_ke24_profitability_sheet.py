from datetime import date
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.pipeline import run_ke24_bigquery_processed as loader
from src.pipeline import run_ke24_profitability_sheet as pipeline
from src.privacy.anonymize_ke24 import generate_token

TEST_KEY = b"a" * 32
COMPANY_TOKEN = generate_token("TEST", "company_code", "TEST", TEST_KEY)
COMPANY_ALIASES = {COMPANY_TOKEN: "NGAA"}


def sample_row():
    return {
        "company_code": COMPANY_TOKEN,
        "period_key": "2026-08",
        "currency_type": "10",
        "currency": "EUR",
        **dict.fromkeys(pipeline.METRICS, 0),
        "total_lancamentos": 12,
        "contribution_margin": -123.45,
    }


def test_preview_reads_bigquery_and_local_aliases_without_authenticating_sheets(tmp_path):
    alias_file = tmp_path / "company_aliases.json"
    alias_file.write_text(
        '{"schema_version": 1, "companies": [{"company_code": "TEST", "company_label": "NGAA"}]}',
        encoding="utf-8",
    )
    client = Mock()
    client.query.return_value.result.return_value = [sample_row()]
    with (
        patch.object(pipeline, "load_privacy_runtime", return_value=({}, {}, TEST_KEY)),
        patch("src.review.ke24_review_company_aliases.alias_file_path", return_value=alias_file),
        patch.object(pipeline, "authenticate_write") as authenticate,
    ):
        pipeline.sync_profitability(client)
        client.query.return_value.result.return_value = [
            dict(sample_row(), company_code="TKN_" + "b" * 32)
        ]
        with pytest.raises(ValueError, match="alias cadastrado"):
            pipeline.sync_profitability(client, spreadsheet_id="sheet")
    authenticate.assert_not_called()
    sql = client.query.call_args.args[0]
    assert "contribution_margin" in sql
    assert "c_4rm_inbound_freight" in sql


def test_matrix_formats_period_and_rejects_duplicate_or_unprotected_groups():
    record = sample_row()
    header, row = pipeline.build_profitability_matrix([record], COMPANY_ALIASES)
    assert header[0] == "company_code"
    assert row[0] == "NGAA"
    assert row[2] == (date(2026, 8, 1) - date(1899, 12, 30)).days
    assert row[3:5] == ["10", "EUR"]
    assert row[pipeline.HEADERS.index("contribution_margin")] == -123.45

    with pytest.raises(ValueError, match="duplicadas"):
        pipeline.build_profitability_matrix([record, record], COMPANY_ALIASES)
    with pytest.raises(ValueError, match="protegido"):
        pipeline.build_profitability_matrix(
            [dict(record, company_code="raw-company")], COMPANY_ALIASES
        )


def test_publish_replaces_and_clears_stale_rows_then_skips_identical_writes():
    desired = pipeline.build_profitability_matrix([sample_row()], COMPANY_ALIASES)
    old_row = [COMPANY_TOKEN, *desired[1][1:]]
    existing = [desired[0], old_row, old_row]
    sheets = Mock()
    target = {"sheetId": 1, "gridProperties": {"rowCount": 1000}}
    with (
        patch.object(pipeline, "inspect_sheet", return_value=target),
        patch.object(pipeline, "read_sheet_matrix", side_effect=[existing, desired, desired]),
    ):
        assert pipeline.publish_profitability_matrix(sheets, Mock(), "sheet", desired)
        assert not pipeline.publish_profitability_matrix(sheets, Mock(), "sheet", desired)

    batch = sheets.spreadsheets.return_value.batchUpdate
    batch.assert_called_once()
    cells, date_format = batch.call_args.kwargs["body"]["requests"]
    assert cells["updateCells"]["range"]["endRowIndex"] == 3
    assert len(cells["updateCells"]["rows"]) == 2
    assert cells["updateCells"]["rows"][1]["values"][0] == {
        "userEnteredValue": {"stringValue": "NGAA"},
    }
    assert cells["updateCells"]["rows"][1]["values"][2] == {
        "userEnteredValue": {"numberValue": desired[1][2]},
    }
    assert date_format["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]["type"] == "DATE"


def test_processed_load_syncs_only_after_validation():
    record = pd.DataFrame([sample_row()])
    operations = Mock()
    with (
        patch.object(loader, "get_profitability_spreadsheet_id", return_value="sheet"),
        patch.object(loader, "load_privacy_runtime", return_value=({}, {}, None)),
        patch.object(loader, "authenticate"),
        patch.object(loader, "build"),
        patch.object(loader, "load_verified_processed_dataframe", return_value=record),
        patch.object(loader.bigquery, "Client"),
        patch.object(loader, "publish_processed_snapshot", operations.load),
        patch.object(loader, "sync_profitability", operations.sync),
    ):
        loader.main(["--publish", "--sync-profitability"])
        assert [call[0] for call in operations.mock_calls] == ["load", "sync"]
        operations.sync.reset_mock()
        operations.load.side_effect = RuntimeError("contagem divergente")
        with pytest.raises(RuntimeError, match="divergente"):
            loader.main(["--publish", "--sync-profitability"])
        operations.sync.assert_not_called()
