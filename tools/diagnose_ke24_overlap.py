from __future__ import annotations

from collections import Counter

import pandas as pd


DIMENSIONS = (
    ("empresa isoladamente", ("company_code",)),
    ("competencia isoladamente", ("period_key",)),
    ("empresa + competencia", ("company_code", "period_key")),
)


def _keys(frame: pd.DataFrame, fields: tuple[str, ...]) -> Counter:
    counts = Counter()
    for row in frame.loc[:, list(fields)].itertuples(index=False, name=None):
        cleaned = []
        for value in row:
            if value is None or pd.isna(value):
                break
            normalized = str(value).strip()
            if not normalized:
                break
            cleaned.append(normalized)
        else:
            counts[tuple(cleaned)] += 1
    return counts


def summarize_overlaps(history: pd.DataFrame, current: pd.DataFrame) -> list[dict]:
    """Devolve apenas contagens; nenhuma chave da KE24 aparece no resultado."""
    required = {field for _, fields in DIMENSIONS for field in fields}
    for name, frame in (("historico", history), ("candidatos", current)):
        if not isinstance(frame, pd.DataFrame):
            raise ValueError(f"Conjunto {name} invalido")
        if frame.columns.duplicated().any():
            raise ValueError(f"Colunas duplicadas no conjunto {name}")
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Faltam colunas esperadas no conjunto {name}")

    result = []
    for label, fields in DIMENSIONS:
        old = _keys(history, fields)
        new = _keys(current, fields)
        shared = old.keys() & new.keys()
        result.append({
            "dimensao": label,
            "historico_chaves_distintas": len(old),
            "atual_chaves_distintas": len(new),
            "chaves_compartilhadas": len(shared),
            "candidatos_com_chave_compartilhada": sum(new[k] for k in shared),
            "historicos_com_chave_compartilhada": sum(old[k] for k in shared),
        })
    return result


def print_summary(stats: list[dict]):
    print("\nKE24 7.6 - Diagnostico complementar (SOMENTE LEITURA)")
    print("Comparacoes independentes, apenas contagens agregadas:\n")
    for info in stats:
        print(f"- {info['dimensao']}")
        print(f"  Chaves distintas (historico / atual): "
              f"{info['historico_chaves_distintas']} / {info['atual_chaves_distintas']}")
        print(f"  Chaves compartilhadas: {info['chaves_compartilhadas']}")
        print(f"  Alertas historicos com chave compartilhada: "
              f"{info['historicos_com_chave_compartilhada']}")
        print(f"  Candidatos atuais com chave compartilhada: "
              f"{info['candidatos_com_chave_compartilhada']}")
    print("\nNenhum arquivo, alerta, status ou planilha foi alterado.")


def main():
    from datetime import datetime, timezone
    from googleapiclient.discovery import build

    from src.ingestion.google_drive_client import authenticate
    from src.loading.ke24_drive_alert_history import load_alert_history_state
    from src.pipeline.run_ke24_drive_features import (
        build_features_in_memory, load_verified_processed_dataframe,
    )
    from src.pipeline.run_ke24_drive_alert_preview import build_alert_preview_in_memory
    from src.pipeline.run_ke24_drive_alert_outputs import build_pending_alert_outputs_in_memory
    from src.privacy.privacy_runtime import load_privacy_runtime

    policy, schema, _ = load_privacy_runtime()
    service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    history = load_alert_history_state(service, policy).history
    processed = load_verified_processed_dataframe(service, policy, schema)
    features, manifest, _ = build_features_in_memory(processed, policy)
    preview = build_alert_preview_in_memory(features, manifest, policy)
    current = build_pending_alert_outputs_in_memory(
        preview["candidates"], policy, datetime.now(timezone.utc)
    )["history"]
    print_summary(summarize_overlaps(history, current))


if __name__ == "__main__":
    main()
