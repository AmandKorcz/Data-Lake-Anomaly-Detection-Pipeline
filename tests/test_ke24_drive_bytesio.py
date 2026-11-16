from io import BytesIO

import pandas as pd
import pytest

from src.ingestion.prepare_ke24 import calculate_file_hash, process_ke24_file
from src.quality.validate_ke24 import load_schema


def synthetic_ke24_xlsx():
    columns = load_schema()["columns"]
    begin = columns.index("sales_quantity")
    values = {name: (0.0 if i >= begin else "TESTE")
              for i, name in enumerate(columns)}
    values.update({
        "period": 3,
        "period_year": "03.2026",
        "company_code": "EMPRESA_TESTE",
        "currency": "USD",
        "sales_quantity": 5.0,
    })
    buffer = BytesIO()
    pd.DataFrame([values], columns=columns).to_excel(buffer, index=False)
    return buffer.getvalue()


def test_sha256_local_e_memoria_iguais(tmp_path):
    payload = synthetic_ke24_xlsx()
    file = tmp_path / "ke24_sintetico.xlsx"
    file.write_bytes(payload)
    stream = BytesIO(payload)
    stream.seek(3)
    assert calculate_file_hash(file) == calculate_file_hash(stream)
    assert stream.tell() == 3


def test_processamento_local_e_memoria_equivalentes(tmp_path):
    payload = synthetic_ke24_xlsx()
    file = tmp_path / "ke24_sintetico.xlsx"
    file.write_bytes(payload)
    schema = load_schema()

    local, _, columns_local, count_local = process_ke24_file(
        file, expected_columns=schema["columns"]
    )
    memory, _, columns_memory, count_memory = process_ke24_file(
        BytesIO(payload),
        expected_columns=schema["columns"],
        source_name=file.name,
    )

    # UUID e timestamp são únicos por execução, logo devem ser excluídos.
    volatile = ["_load_id", "_ingested_at_utc"]
    pd.testing.assert_frame_equal(
        local.drop(columns=volatile),
        memory.drop(columns=volatile),
        check_dtype=True,
    )
    assert columns_local == columns_memory == schema["columns"]
    assert count_local == count_memory == 1


def test_layout_reordenado_e_bloqueado(tmp_path):
    columns = load_schema()["columns"]
    payload = synthetic_ke24_xlsx()
    frame = pd.read_excel(BytesIO(payload))
    frame = frame[[columns[1], columns[0], *columns[2:]]]
    output = BytesIO()
    frame.to_excel(output, index=False)

    with pytest.raises(ValueError, match="layout|ordem|Ordem"):
        process_ke24_file(
            BytesIO(output.getvalue()),
            expected_columns=columns,
            source_name="ke24_layout_erro.xlsx",
        )
