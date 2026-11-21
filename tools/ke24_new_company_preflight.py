from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd

from src.loading.plan_ke24_incremental import MANIFEST_COLUMNS

_TOKEN = re.compile(r"TKN_[0-9a-f]{32}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_PERIOD_YEAR = re.compile(r"(\d{1,3})\.(\d{4})\Z")
_MIN_TRAIN = 20


@dataclass(frozen=True)
class GroupCheck:
    company_token: str
    currency: str
    new_company: bool
    new_group: bool
    incoming_files: int
    incoming_rows: int
    periods: int
    training_rows: int
    validation_rows: int
    ready_for_model: bool
    alias_available: bool


@dataclass(frozen=True)
class CompanyPreflight:
    committed_files: int
    incoming_files: int
    existing_companies: int
    new_companies: int
    total_companies: int
    incoming_rows: int
    incoming_groups: int
    missing_alias_companies: int
    model_blocking_groups: int
    groups: tuple[GroupCheck, ...]

    @property
    def ready_for_e2e(self) -> bool:
        return self.missing_alias_companies == 0 and self.model_blocking_groups == 0


def _checked_manifest(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
        raise ValueError(f"{name}: manifesto invalido.")
    if not set(MANIFEST_COLUMNS).issubset(frame.columns):
        raise ValueError(f"{name}: colunas de manifesto ausentes.")
    out = frame.loc[:, MANIFEST_COLUMNS].copy()
    if out.empty:
        out["_period_date"] = pd.Series(dtype="datetime64[ns]")
        return out
    if out.isna().any().any():
        raise ValueError(f"{name}: metadados incompletos.")
    if not out["company_code"].map(lambda x: isinstance(x, str) and _TOKEN.fullmatch(x) is not None).all():
        raise ValueError(f"{name}: empresa sem token valido.")
    if not out["currency"].map(lambda x: isinstance(x, str) and _CURRENCY.fullmatch(x) is not None).all():
        raise ValueError(f"{name}: moeda invalida.")
    if not out["_source_file_sha256"].map(lambda x: isinstance(x, str) and _TOKEN.fullmatch(x) is not None).all():
        raise ValueError(f"{name}: origem sem token valido.")
    if out["_source_file_sha256"].duplicated().any():
        raise ValueError(f"{name}: origem repetida.")
    counts = pd.to_numeric(out["record_count"], errors="coerce")
    periods = pd.to_numeric(out["period"], errors="coerce")
    if (counts.isna().any() or not counts.ge(1).all() or not counts.mod(1).eq(0).all()
            or periods.isna().any() or not periods.between(1,12).all()
            or not periods.mod(1).eq(0).all()):
        raise ValueError(f"{name}: periodos ou quantidades invalidos.")
    dates = []
    for year_code, period in zip(out["period_year"], periods):
        match = _PERIOD_YEAR.fullmatch(str(year_code))
        if match is None or int(match.group(1)) != int(period):
            raise ValueError(f"{name}: competencia inconsistente.")
        dates.append(pd.Timestamp(year=int(match.group(2)), month=int(period), day=1))
    out["_period_date"] = pd.to_datetime(dates)
    out["record_count"] = counts.astype("int64")
    if out.duplicated(["company_code", "currency", "_period_date"]).any():
        raise ValueError(f"{name}: empresa, moeda e competencia repetidas.")
    return out


def validate_new_model_currency_type(staging: pd.DataFrame) -> None:
    if (not isinstance(staging, pd.DataFrame)
            or staging.columns.duplicated().any()
            or "currency_type" not in staging.columns
            or staging.empty):
        raise ValueError("Currency type ausente ou estrutura invalida.")
    numeric = pd.to_numeric(staging["currency_type"], errors="coerce")
    if not numeric.eq(10).all():
        raise ValueError("Novas extracoes possuem currency_type nao suportado pelo modelo.")


def plan_new_company_preflight(
    committed_manifest: pd.DataFrame,
    incoming_manifest: pd.DataFrame,
    company_aliases: dict[str, str],
) -> CompanyPreflight:
    existing = _checked_manifest(committed_manifest, "Historico")
    incoming = _checked_manifest(incoming_manifest, "Entradas")
    if not isinstance(company_aliases, dict) or any(
        not isinstance(k, str) or _TOKEN.fullmatch(k) is None
        or not isinstance(v, str) or not v.strip()
        for k, v in company_aliases.items()
    ):
        raise ValueError("Aliases privados invalidos.")
    old_sources = set(existing["_source_file_sha256"])
    new_sources = set(incoming["_source_file_sha256"])
    if old_sources & new_sources:
        raise ValueError("Uma extracao de entrada ja consta no manifesto.")

    combined = pd.concat([existing, incoming], ignore_index=True)
    if combined.duplicated(["company_code", "currency", "_period_date"]).any():
        raise ValueError("Competencia ja existente para mesma empresa e moeda.")
    old_companies = set(existing["company_code"])
    incoming_companies = set(incoming["company_code"])
    all_companies = set(combined["company_code"])
    new_companies = incoming_companies - old_companies
    old_groups = set(zip(existing["company_code"], existing["currency"]))
    incoming_groups = set(zip(incoming["company_code"], incoming["currency"]))

    groups = []
    for (company, currency), group in combined.groupby(["company_code", "currency"], sort=True):
        last_period = group["_period_date"].max()
        train_rows = int(group.loc[group["_period_date"].lt(last_period), "record_count"].sum())
        valid_rows = int(group.loc[group["_period_date"].eq(last_period), "record_count"].sum())
        in_group = incoming.loc[
            incoming["company_code"].eq(company) & incoming["currency"].eq(currency)
        ]
        groups.append(GroupCheck(
            company_token=company,
            currency=currency,
            new_company=company in new_companies,
            new_group=(company, currency) not in old_groups,
            incoming_files=len(in_group),
            incoming_rows=int(in_group["record_count"].sum()),
            periods=int(group["_period_date"].nunique()),
            training_rows=train_rows,
            validation_rows=valid_rows,
            ready_for_model=train_rows >= _MIN_TRAIN and valid_rows > 0,
            alias_available=company in company_aliases,
        ))
    return CompanyPreflight(
        committed_files=len(existing),
        incoming_files=len(incoming),
        existing_companies=len(old_companies),
        new_companies=len(new_companies),
        total_companies=len(all_companies),
        incoming_rows=int(incoming["record_count"].sum()),
        incoming_groups=len(incoming_groups),
        missing_alias_companies=len(all_companies - set(company_aliases)),
        model_blocking_groups=sum(not g.ready_for_model for g in groups),
        groups=tuple(groups),
    )


def format_preflight(report: CompanyPreflight, aliases: dict[str, str]) -> str:
    if not isinstance(report, CompanyPreflight):
        raise TypeError("Relatorio invalido.")
    missing = sorted({g.company_token for g in report.groups if not g.alias_available})
    redacted = {token: f"Empresa sem alias #{i}" for i, token in enumerate(missing, 1)}
    lines = [
        "KE24 - Preflight de novas empresas (SOMENTE LEITURA)",
        f"Extracoes commitadas: {report.committed_files}",
        f"Extracoes novas: {report.incoming_files}",
        f"Registros novos: {report.incoming_rows}",
        f"Empresas no historico: {report.existing_companies}",
        f"Empresas novas nos XLSX: {report.new_companies}",
        f"Empresas apos a carga: {report.total_companies}",
        f"Grupos empresa/moeda nas entradas: {report.incoming_groups}",
        f"Empresas sem alias privado: {report.missing_alias_companies}",
        f"Grupos com treino temporal insuficiente: {report.model_blocking_groups}",
    ]
    if report.incoming_files == 0:
        lines.append("Nenhuma extracao nova: baseline mantida; nenhum dado publicado.")
    else:
        lines.append("Diagnostico dos grupos afetados (sem codigos/tokens):")
        for g in report.groups:
            if g.incoming_files == 0:
                continue
            label = aliases[g.company_token] if g.alias_available else redacted[g.company_token]
            status = "OK" if g.ready_for_model and g.alias_available else "PENDENTE"
            lines.append(
                f"  {label} / {g.currency}: "
                f"empresa_nova={'sim' if g.new_company else 'nao'}; "
                f"grupo_novo={'sim' if g.new_group else 'nao'}; "
                f"arquivos={g.incoming_files}; periodos={g.periods}; "
                f"treino={g.training_rows}; validacao={g.validation_rows}; "
                f"alias={'OK' if g.alias_available else 'AUSENTE'}; status={status}"
            )
    lines.append("Preflight E2E: " + ("APROVADO" if report.ready_for_e2e else "PENDENTE"))
    lines.append("Nenhum arquivo, manifesto, alerta ou planilha foi alterado.")
    lines.append("Nao comprova qualidade estatistica nem autoriza publicacao automatica.")
    return "\n".join(lines)


def _read_only_preview():
    from googleapiclient.discovery import build
    from src.ingestion.google_drive_client import (
        authenticate, list_raw_xlsx_files, read_xlsx_from_drive,
    )
    from src.ingestion.prepare_ke24 import process_ke24_file
    from src.loading.ke24_drive_manifest import load_processed_manifest
    from src.loading.ke24_drive_source_selection import collect_new_drive_frames
    from src.loading.plan_ke24_incremental import (
        build_file_manifest, plan_incremental_load,
    )
    from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
    from src.privacy.privacy_runtime import load_privacy_runtime
    from src.privacy.validate_protected_dataset import validate_protected_dataset
    from src.quality.validate_ke24 import (
        load_schema, load_quality_rules, validate_schema, validate_metadata,
        validate_required_fields, validate_integer_like_columns,
        validate_numeric_measures,
    )
    from src.review.ke24_review_company_aliases import load_private_company_aliases

    privacy, privacy_schema, key = load_privacy_runtime()
    schema, rules = load_schema(), load_quality_rules()
    service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    committed = load_processed_manifest(service)
    raw = list_raw_xlsx_files(service)
    frames, selection = collect_new_drive_frames(
        service, raw, committed, key,
        expected_columns=list(schema["columns"]),
        privacy_policy=privacy, privacy_schema=privacy_schema,
        download=read_xlsx_from_drive, parse=process_ke24_file,
        anonymize=anonymize_ke24_dataframe, validate=validate_protected_dataset,
    )
    if frames:
        staging = pd.concat(frames, ignore_index=True)
        errors = [
            *validate_schema(staging, schema), *validate_metadata(staging),
            *validate_required_fields(staging, rules),
            *validate_integer_like_columns(staging, rules),
            *validate_numeric_measures(staging, schema, rules),
        ]
        if errors:
            raise ValueError("Validacao de qualidade das novas extracoes reprovada.")
        validate_new_model_currency_type(staging)
        plan = plan_incremental_load(staging, committed)
        if plan["new_files"] != selection["new_files"] or plan["skipped_files"]:
            raise RuntimeError("Manifesto e fontes novas divergentes.")
        incoming = build_file_manifest(plan["new_rows"])
    else:
        incoming = pd.DataFrame(columns=MANIFEST_COLUMNS)
    aliases = load_private_company_aliases(key)
    result = plan_new_company_preflight(committed, incoming, aliases)
    return result, aliases


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Valida empresas novas no raw sem gravar nada no Drive/Sheets."
    )
    parser.parse_args(argv)
    try:
        report, aliases = _read_only_preview()
        print(format_preflight(report, aliases))
        return 0 if report.ready_for_e2e else 2
    except Exception as exc:
        print(f"Preflight REPROVADO ({type(exc).__name__}). "
              "Verifique a etapa em ambiente restrito; dados omitidos.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
