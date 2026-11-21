from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

TOKEN = re.compile(r"TKN_[0-9a-f]{32}\Z")
ALERT = re.compile(r"ALT_[0-9a-f]{32}\Z")
PERIOD = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
MEASURE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
STATUSES = frozenset({
    "PENDING", "IN_REVIEW", "CONFIRMED_ERROR", "CORRECTED", "NOT_AN_ERROR"
})
BUSINESS_FIELDS = (
    "company_code", "reference_document", "period_key",
    "profit_center", "product", "currency", "currency_type",
    "dominant_measure", "signal_method",
    "total_absolute_value", "dominant_measure_value",
)


@dataclass(frozen=True)
class RecurrencePlan:
    eligible: pd.DataFrame
    matched_exact_id: int
    matched_business: int
    suppressed_reviewed: int
    duplicates_pending: int
    ambiguous_candidates: int
    unmatchable_candidates: int
    safe_unmatchable_candidates: int = 0

    @property
    def publication_blockers(self) -> int:
        return (self.ambiguous_candidates + self.unmatchable_candidates
                - self.safe_unmatchable_candidates)


def _protected(value, *, required):
    if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError("Identificador protegido obrigatorio ausente.")
        return None
    if not isinstance(value, str) or not TOKEN.fullmatch(value.strip()):
        raise ValueError("Identificador de negocio sem protecao valida.")
    return value.strip()


def _amount(row, field):
    try:
        value = Decimal(str(row[field]))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value.normalize() if value.is_finite() else None


def _business_key(row, *, allow_missing_document=False):
    company = _protected(row["company_code"], required=True)
    document = _protected(row["reference_document"], required=False)
    if document is None and not allow_missing_document:
        return None
    period = str(row["period_key"]).strip()
    if not PERIOD.fullmatch(period):
        raise ValueError("Competencia invalida para recorrencia.")
    profit_center = _protected(row["profit_center"], required=False)
    product = _protected(row["product"], required=False)
    currency = str(row["currency"]).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Moeda invalida para recorrencia.")
    kind = pd.to_numeric(row["currency_type"], errors="coerce")
    if pd.isna(kind) or not float(kind).is_integer() or kind <= 0:
        raise ValueError("Tipo de moeda invalido para recorrencia.")
    measure = str(row["dominant_measure"]).strip()
    if not MEASURE.fullmatch(measure):
        return None
    signal = str(row["signal_method"]).strip()
    if signal != "training_p95":
        raise ValueError("Metodo de sinal desconhecido para recorrencia.")
    total = _amount(row, "total_absolute_value")
    dominant = _amount(row, "dominant_measure_value")
    if total is None or total < 0 or dominant is None:
        return None
    return (
        company, document, period, profit_center, product,
        currency, int(kind), measure, signal, total, dominant,
    )



def _valid_source_row_number(row):
    try:
        value = pd.to_numeric(row.get("_source_row_number"), errors="coerce")
        if pd.isna(value) or not float(value).is_integer() or value < 2:
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _provably_distinct_from_incomplete_history(current, historical_rows):
    candidate_partial = _business_key(current, allow_missing_document=True)
    if candidate_partial is None:
        return False
    candidate_partial = (candidate_partial[0], *candidate_partial[2:])
    candidate_file = _protected(current.get("_source_file_sha256"), required=False)
    candidate_line = _valid_source_row_number(current)
    candidate_currency = str(current["currency"]).strip().upper()

    for prior in historical_rows:
        if _protected(prior["reference_document"], required=False) is not None:
            return False  # Falta outro campo alem do documento.
        prior_partial = _business_key(prior, allow_missing_document=True)
        if prior_partial is None:
            return False  # Falta informacao para diferenciar.
        prior_partial = (prior_partial[0], *prior_partial[2:])
        if candidate_partial == prior_partial:
            return False  # Potencial recorrencia de negocio.
        if str(prior["currency"]).strip().upper() != candidate_currency:
            continue  # Moeda e parte obrigatoria do contrato de negocio.
        prior_file = _protected(prior.get("_source_file_sha256"), required=False)
        prior_line = _valid_source_row_number(prior)
        if (candidate_file is None or prior_file != candidate_file
                or candidate_line is None or prior_line is None
                or candidate_line == prior_line):
            return False  # Em exportacoes diferentes a diferenca parcial e incerta.
    return True

def _validate_frame(frame, label):
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
        raise ValueError(f"{label}: estrutura invalida.")
    if not {"alert_id", *BUSINESS_FIELDS}.issubset(frame.columns):
        raise ValueError(f"{label}: campos obrigatorios ausentes.")
    ids = frame["alert_id"].tolist()
    if (any(not isinstance(aid, str) or ALERT.fullmatch(aid) is None
            for aid in ids) or len(set(ids)) != len(ids)):
        raise ValueError(f"{label}: IDs de alerta invalidos ou duplicados.")
    for value in frame["company_code"]:
        _protected(value, required=True)
    for value in frame["reference_document"]:
        _protected(value, required=False)


def plan_business_recurrences(previous, current, statuses):
    _validate_frame(previous, "Historico")
    _validate_frame(current, "Candidatos")
    if not isinstance(statuses, dict) or any(
        not isinstance(k, str) or v not in STATUSES for k, v in statuses.items()
    ):
        raise ValueError("Mapa de status operacional invalido.")
    old_ids = set(previous["alert_id"])
    if set(statuses) != old_ids:
        raise ValueError("Status operacionais nao cobrem integralmente o historico.")

    if previous.empty:
        return RecurrencePlan(current.copy(), 0, 0, 0, 0, 0, 0)

    old_keys = defaultdict(list)
    unknown_old_scopes = defaultdict(list)
    current_keys = defaultdict(list)
    current_partial_keys = defaultdict(list)
    old_rows = previous.to_dict(orient="records")
    new_rows = current.to_dict(orient="records")
    # Escopo histórico é suficiente para excluir recorrência entre competências
    # distintas; nunca é suficiente para suprimir lançamentos na mesma competência.
    old_scopes = {(row["company_code"], row["period_key"]) for row in old_rows}
    for row in old_rows:
        key = _business_key(row)
        if key is not None:
            old_keys[key].append(row["alert_id"])
        else:
            unknown_old_scopes[(row["company_code"], row["period_key"])].append(row)
    for row in new_rows:
        key = _business_key(row)
        if key is not None:
            current_keys[key].append(row["alert_id"])
        # Chave parcial só detecta colisões potenciais dentro do lote atual;
        # ela NUNCA autoriza suprimir um alerta ou afirmar que é recorrente.
        partial = _business_key(row, allow_missing_document=True)
        if partial is not None:
            current_partial_keys[(partial[0], *partial[2:])].append(row["alert_id"])

    keep = []
    exact = cross_export = treated = pending = ambiguous = unmatchable = 0
    safe_unmatchable = 0
    for row in new_rows:
        aid = row["alert_id"]
        prior_id = None
        if aid in old_ids:
            exact += 1
            prior_id = aid
        else:
            key = _business_key(row)
            if key is None:
                unmatchable += 1
                # A ausência de documento NÃO autoriza comparar por valores.
                # Porém, sem histórico na mesma empresa e competência, não
                # existe alerta anterior nesse escopo que possa ser recorrente.
                # Ainda exigimos os demais campos e ausência de colisão parcial
                # com outro candidato deste mesmo lote.
                partial = _business_key(row, allow_missing_document=True)
                if (partial is not None
                        and _protected(row["reference_document"], required=False) is None
                        and (row["company_code"], row["period_key"]) not in old_scopes
                        and len(current_partial_keys[(partial[0], *partial[2:])]) == 1):
                    safe_unmatchable += 1
            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:
                # Historico sem chave completa: so dispensa bloqueio mediante
                # proveniencia comprovada, sem suprimir nem mesclar alertas.
                scope = (row["company_code"], row["period_key"])
                if not (key not in old_keys
                        and len(current_keys[key]) == 1
                        and _provably_distinct_from_incomplete_history(
                            row, unknown_old_scopes[scope])):
                    ambiguous += 1
            elif key in old_keys:
                if len(old_keys[key]) == 1 and len(current_keys[key]) == 1:
                    prior_id = old_keys[key][0]
                    cross_export += 1
                else:
                    ambiguous += 1
        if prior_id is None:
            keep.append(aid)
        elif statuses[prior_id] == "PENDING":
            pending += 1
        else:
            treated += 1

    return RecurrencePlan(
        eligible=current.loc[current["alert_id"].isin(keep)].copy(),
        matched_exact_id=exact,
        matched_business=cross_export,
        suppressed_reviewed=treated,
        duplicates_pending=pending,
        ambiguous_candidates=ambiguous,
        unmatchable_candidates=unmatchable,
        safe_unmatchable_candidates=safe_unmatchable,
    )
