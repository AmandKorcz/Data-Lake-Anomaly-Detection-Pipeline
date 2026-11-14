import hashlib
import json
import os
import re
from dataclasses import dataclass
from io import BytesIO

import pandas as pd
from googleapiclient.http import MediaIoBaseUpload

from src.alerts.build_ke24_alert_records import generate_alert_id
from src.alerts.build_ke24_pretrained_outputs import (
    HISTORY_COLUMNS,
    build_pretrained_dashboard,
    validate_pretrained_history,
)

ALERTS_FOLDER_ENV = "KE24_DRIVE_ALERTS_FOLDER_ID"
MANIFEST_NAME = "ke24_alert_history_manifest.json"
SCHEMA_VERSION = 1
SNAPSHOT_FIELDS = {"file_id", "file_name", "sha256", "byte_count", "record_count"}
FOLDER_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}")
ID_PATTERN = re.compile(r"ALT_[0-9a-f]{32}")


@dataclass
class HistoryState:
    history: pd.DataFrame
    snapshots: list
    manifest_id: str | None
    manifest_sha256: str | None


def get_alerts_folder_id():
    folder = os.getenv(ALERTS_FOLDER_ENV, "").strip()
    if not FOLDER_PATTERN.fullmatch(folder):
        raise ValueError(f"Configure {ALERTS_FOLDER_ENV} com um ID de pasta valido.")
    for name in ("KE24_DRIVE_PROCESSED_FOLDER_ID", "KE24_DRIVE_RAW_FOLDER_ID"):
        if folder == os.getenv(name, "").strip():
            raise ValueError("A pasta de alertas deve ser diferente das pastas raw e processed.")
    return folder


def list_alert_files(service):
    folder = get_alerts_folder_id()
    page_token = None
    found = []
    while True:
        page = service.files().list(
            q=f"'{folder}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=100,
            pageToken=page_token,
        ).execute()
        found.extend(page.get("files", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return found


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _validate_manifest(doc):
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "snapshots"}:
        raise RuntimeError("Manifesto de alertas invalido.")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("Versao do manifesto de alertas nao suportada.")
    snapshots = doc["snapshots"]
    if not isinstance(snapshots, list) or not snapshots:
        raise RuntimeError("Manifesto de alertas sem snapshots.")
    ids, names = set(), set()
    for snap in snapshots:
        if not isinstance(snap, dict) or set(snap) != SNAPSHOT_FIELDS:
            raise RuntimeError("Metadados de snapshot invalidos.")
        if not isinstance(snap["file_id"], str) or not snap["file_id"]:
            raise RuntimeError("ID do snapshot invalido.")
        if not isinstance(snap["sha256"], str) or not SHA_PATTERN.fullmatch(snap["sha256"]):
            raise RuntimeError("Hash do snapshot invalido.")
        expected_name = f"ke24_alert_history_{snap['sha256'][:16]}.csv"
        if snap["file_name"] != expected_name:
            raise RuntimeError("Nome do snapshot nao corresponde ao hash.")
        for field in ("byte_count", "record_count"):
            if (not isinstance(snap[field], int) or isinstance(snap[field], bool)
                    or snap[field] <= 0):
                raise RuntimeError("Contagem do snapshot invalida.")
        if snap["file_id"] in ids or snap["file_name"] in names:
            raise RuntimeError("Snapshots duplicados no manifesto.")
        ids.add(snap["file_id"])
        names.add(snap["file_name"])
    return snapshots


def _validate_alert_ids(history):
    if history.empty:
        return
    if not history["alert_id"].astype("string").str.fullmatch(ID_PATTERN.pattern).fillna(False).all():
        raise ValueError("IDs de alertas invalidos.")
    expected = [
        generate_alert_id(source, int(number), company)
        for source, number, company in zip(
            history["_source_file_sha256"],
            history["_source_row_number"],
            history["company_code"],
        )
    ]
    if expected != history["alert_id"].tolist():
        raise ValueError("IDs nao correspondem as identidades protegidas.")


def validate_history(history, policy):
    validate_pretrained_history(history, policy)
    _validate_alert_ids(history)


def _read_snapshot(payload, expected_count, policy):
    # Usar o contrato das colunas ao inves de inferir colunas arbitrarias.
    frame = pd.read_csv(BytesIO(payload), encoding="utf-8", low_memory=False)
    if list(frame.columns) != HISTORY_COLUMNS or len(frame) != expected_count:
        raise RuntimeError("Estrutura ou numero de alertas difere do manifesto.")
    validate_history(frame, policy)
    return frame


def load_alert_history_state(service, policy):
    found = list_alert_files(service)
    if not found:
        return HistoryState(pd.DataFrame(columns=HISTORY_COLUMNS), [], None, None)
    manifests = [item for item in found if item["name"] == MANIFEST_NAME]
    if len(manifests) != 1:
        raise RuntimeError("Pasta de alertas sem manifesto unico; reconciliacao manual necessaria.")
    manifest_file = manifests[0]
    raw = service.files().get_media(fileId=manifest_file["id"]).execute()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Manifesto de alertas ilegivel.") from exc
    snaps = _validate_manifest(doc)
    expected = {manifest_file["id"]} | {snap["file_id"] for snap in snaps}
    by_id = {item["id"]: item for item in found}
    if len(by_id) != len(found) or set(by_id) != expected:
        raise RuntimeError("Arquivos de alertas ausentes, duplicados ou orfaos; reconciliar.")

    last_payload = None
    for snap in snaps:
        item = by_id[snap["file_id"]]
        if item["name"] != snap["file_name"]:
            raise RuntimeError("Nome remoto do snapshot divergente.")
        payload = service.files().get_media(fileId=snap["file_id"]).execute()
        if len(payload) != snap["byte_count"] or _sha(payload) != snap["sha256"]:
            raise RuntimeError("SHA-256 de snapshot de alertas invalido: reconciliar.")
        last_payload = payload

    frame = _read_snapshot(last_payload, snaps[-1]["record_count"], policy)
    return HistoryState(frame, snaps, manifest_file["id"], _sha(raw))


def append_new_alerts(previous, current, policy):
    if not isinstance(previous, pd.DataFrame) or not isinstance(current, pd.DataFrame):
        raise TypeError("Historico e candidatos precisam ser DataFrames.")
    if list(previous.columns) != HISTORY_COLUMNS or list(current.columns) != HISTORY_COLUMNS:
        raise ValueError("Colunas do historico incompatíveis com o contrato.")
    validate_history(previous, policy)
    validate_history(current, policy)
    old_ids = set(previous["alert_id"].tolist())
    only_new = current.loc[~current["alert_id"].isin(old_ids)].copy()
    if previous.empty:
        merged = only_new.copy()
    elif only_new.empty:
        merged = previous.copy()
    else:
        merged = pd.concat([previous, only_new], ignore_index=True)
    if not merged.empty:
        merged = merged.sort_values(["period_key", "alert_id"]).reset_index(drop=True)
    validate_history(merged, policy)
    return merged, len(only_new)


def serialize_history(history, policy):
    if history.empty:
        raise ValueError("Nao publicar historico vazio.")
    if list(history.columns) != HISTORY_COLUMNS:
        raise ValueError("Contrato de colunas invalido.")
    validate_history(history, policy)
    return history.to_csv(index=False, lineterminator="\n", float_format="%.17g").encode("utf-8")


def _upload_and_verify(service, folder, name, payload, mime_type):
    created = service.files().create(
        body={"name": name, "parents": [folder], "mimeType": mime_type},
        media_body=MediaIoBaseUpload(BytesIO(payload), mimetype=mime_type, resumable=False),
        fields="id,name,parents",
    ).execute()
    if (not created.get("id") or created.get("name") != name
            or folder not in created.get("parents", [])):
        raise RuntimeError("Metadados do upload de alertas inconsistentes.")
    received = service.files().get_media(fileId=created["id"]).execute()
    if len(received) != len(payload) or _sha(received) != _sha(payload):
        raise RuntimeError("Verificacao de upload de alertas falhou.")
    return created["id"]


def _same_state(expected, observed):
    return (expected.manifest_id == observed.manifest_id
            and expected.manifest_sha256 == observed.manifest_sha256
            and expected.snapshots == observed.snapshots)


def publish_alert_history(read_service, write_service, expected_state, history, policy):
    folder = get_alerts_folder_id()
    if history.empty:
        raise ValueError("Nao publicar historico vazio.")
    validate_history(history, policy)
    current_state = load_alert_history_state(read_service, policy)
    if not _same_state(expected_state, current_state):
        raise RuntimeError("Historico mudou desde a leitura: refaca a previa.")
    current_ids = set(history["alert_id"])
    prior_ids = set(current_state.history["alert_id"])
    if not prior_ids.issubset(current_ids):
        raise RuntimeError("Publicacao tentaria remover alertas historicos.")
    # Proibe mudar ou rebaixar qualquer registro ja persistido.
    if not current_state.history.empty:
        original = current_state.history.set_index("alert_id")
        candidate = history.set_index("alert_id").loc[original.index]
        try:
            pd.testing.assert_frame_equal(
                candidate, original, check_dtype=False, check_exact=False,
                rtol=0, atol=1e-12,
            )
        except AssertionError as exc:
            raise RuntimeError("Regravacao de alertas historicos bloqueada.") from exc
    if current_ids == prior_ids:
        return {"published": False, "row_count": len(history)}

    # Um escritor pode ter visibilidade menor usando drive.file; bloquear discrepancias.
    readable = {(item["id"], item["name"]) for item in list_alert_files(read_service)}
    writable = {(item["id"], item["name"]) for item in list_alert_files(write_service)}
    if readable != writable:
        raise RuntimeError("Autorizacao de escrita nao enxerga todos os arquivos da pasta.")

    payload = serialize_history(history, policy)
    # Comprova que o CSV podera ser recarregado sem perda de contrato.
    _read_snapshot(payload, len(history), policy)
    digest = _sha(payload)
    name = f"ke24_alert_history_{digest[:16]}.csv"
    if name in {item["file_name"] for item in current_state.snapshots}:
        raise RuntimeError("Snapshot com mesmo nome ja registrado; reconciliacao necessaria.")

    try:
        snapshot_id = _upload_and_verify(write_service, folder, name, payload, "text/csv")
        snap = {
            "file_id": snapshot_id,
            "file_name": name,
            "sha256": digest,
            "byte_count": len(payload),
            "record_count": len(history),
        }
        snapshots = [*current_state.snapshots, snap]
        doc = {"schema_version": SCHEMA_VERSION, "snapshots": snapshots}
        _validate_manifest(doc)
        manifest_payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")

        # Revalidar o manifesto imediatamente antes do commit. Nao e lock atomico;
        # executar de maneira serial para impedir concorrencia.
        if current_state.manifest_id:
            remote = read_service.files().get_media(fileId=current_state.manifest_id).execute()
            if _sha(remote) != current_state.manifest_sha256:
                raise RuntimeError("Manifesto alterado durante a publicacao.")
            updated = write_service.files().update(
                fileId=current_state.manifest_id,
                media_body=MediaIoBaseUpload(
                    BytesIO(manifest_payload), mimetype="application/json", resumable=False
                ),
                fields="id,name,parents",
            ).execute()
            if updated.get("id") != current_state.manifest_id:
                raise RuntimeError("Resposta de atualizacao do manifesto invalida.")
            manifest_id = current_state.manifest_id
        else:
            manifest_id = _upload_and_verify(
                write_service, folder, MANIFEST_NAME, manifest_payload, "application/json"
            )

        verified = read_service.files().get_media(fileId=manifest_id).execute()
        if _sha(verified) != _sha(manifest_payload):
            raise RuntimeError("Manifesto remoto difere do commit enviado.")
        # Valida o estado completo, incluindo o novo snapshot.
        after = load_alert_history_state(read_service, policy)
        if len(after.history) != len(history) or not after.history["alert_id"].equals(history["alert_id"]):
            raise RuntimeError("Estado remoto nao corresponde ao historico esperado.")
    except Exception as exc:
        raise RuntimeError(
            "Publicacao de alertas incompleta ou indeterminada. NAO repita automaticamente: "
            "verifique a pasta alerts e reconcilie snapshots/manifesto."
        ) from exc
    return {"published": True, "row_count": len(history)}
