from __future__ import annotations

import argparse
import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "KE24_DRIVE_READ_RETRY_V1"
VERSION = "6.0.0"
IMPORT = "from src.pipeline.ke24_drive_read_retry import execute_drive_read\n"

CHANGES = {
    "src/ingestion/google_drive_client.py": (
        (
            '        response = service.files().list(\n'
            '            q=query,\n'
            '            fields="nextPageToken, files(id, name, mimeType)",\n'
            '            pageSize=100,\n'
            '            pageToken=page_token\n'
            '        ).execute()',
            '        response = execute_drive_read(lambda: service.files().list(\n'
            '            q=query,\n'
            '            fields="nextPageToken, files(id, name, mimeType)",\n'
            '            pageSize=100,\n'
            '            pageToken=page_token\n'
            '        ))',
        ),
    ),
    "src/loading/ke24_drive_manifest.py": (
        (
            '        page = service.files().list(\n'
            '            q=f"\'{folder_id}\' in parents and trashed = false",\n'
            '            fields="nextPageToken,files(id,name,mimeType)",\n'
            '            pageSize=100,\n'
            '            pageToken=page_token,\n'
            '        ).execute()',
            '        page = execute_drive_read(lambda: service.files().list(\n'
            '            q=f"\'{folder_id}\' in parents and trashed = false",\n'
            '            fields="nextPageToken,files(id,name,mimeType)",\n'
            '            pageSize=100,\n'
            '            pageToken=page_token,\n'
            '        ))',
        ),
        (
            '    content = service.files().get_media(fileId=manifests[0]["id"]).execute()',
            '    content = execute_drive_read(\n'
            '        lambda: service.files().get_media(fileId=manifests[0]["id"])\n'
            '    )',
        ),
        (
            '        payload = service.files().get_media(fileId=batch["parquet_id"]).execute()',
            '        payload = execute_drive_read(\n'
            '            lambda: service.files().get_media(fileId=batch["parquet_id"])\n'
            '        )',
        ),
    ),
    "src/pipeline/run_ke24_drive_features.py": (
        (
            '    raw_manifest = service.files().get_media(\n'
            '        fileId=manifest_files[0]["id"]\n'
            '    ).execute()',
            '    raw_manifest = execute_drive_read(lambda: service.files().get_media(\n'
            '        fileId=manifest_files[0]["id"]\n'
            '    ))',
        ),
        (
            '        payload = service.files().get_media(fileId=parquet_id).execute()',
            '        payload = execute_drive_read(\n'
            '            lambda: service.files().get_media(fileId=parquet_id)\n'
            '        )',
        ),
    ),
}


def transform_source(source: str, changes) -> tuple[str, bool]:
    if not isinstance(source, str):
        raise TypeError("Codigo-fonte invalido.")
    if source.count(MARKER) > 1:
        raise ValueError("Marcador duplicado. Verificar manualmente.")

    old_counts = [source.count(old) for old, _new in changes]
    new_counts = [source.count(new) for _old, new in changes]

    if all(count == 0 for count in old_counts) and all(count == 1 for count in new_counts):
        if source.count(IMPORT) != 1:
            raise ValueError("Import de leitura ausente ou duplicado.")
        if MARKER in source:
            ast.parse(source)
            return source, False
        updated = f"# {MARKER}: leituras somente leitura com retry limitado.\n" + source
        ast.parse(updated)
        return updated, True

    if not all(count == 1 for count in old_counts) or any(new_counts) or MARKER in source:
        raise ValueError("Arquivo modificado ou integracao parcial. Nenhuma alteracao aplicada.")
    if IMPORT in source:
        raise ValueError("Import existente sem os blocos esperados.")
    updated = source
    for old, new in changes:
        updated = updated.replace(old, new, 1)
    updated = f"# {MARKER}: leituras somente leitura com retry limitado.\n{IMPORT}" + updated
    if (updated.count(MARKER) != 1
            or any(updated.count(new) != 1 for _, new in changes)
            or any(old in updated for old, _ in changes)):
        raise ValueError("Integracao incompleta, sem gravacao.")
    ast.parse(updated)
    return updated, True


def plan(root=ROOT):
    root = Path(root)
    staged = {}
    for relative, changes in CHANGES.items():
        target = root / relative
        if not target.is_file():
            raise FileNotFoundError("Arquivo Python esperado nao encontrado.")
        source = target.read_text(encoding="utf-8")
        updated, changed = transform_source(source, changes)
        staged[target] = (source, updated, changed)
    return staged


def install(root=ROOT, *, apply=False):
    staged = plan(root)
    changed = [p for p, (_old, _new, needs) in staged.items() if needs]
    if not apply or not changed:
        return len(changed)

    written = []
    try:
        for path in changed:
            original, new, _ = staged[path]
            if path.read_text(encoding="utf-8") != original:
                raise RuntimeError("Arquivo alterado apos verificacao; nenhuma aplicacao concluida.")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="\n", dir=path.parent,
                    prefix=".ke24-drive-retry-", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(new)
                os.replace(temporary, path)
                written.append((path, original))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except BaseException:
        for path, old in reversed(written):
            path.write_text(old, encoding="utf-8", newline="\n")
        raise
    return len(changed)


def _self_test():
    for changes in CHANGES.values():
        stub = "# exemplo sintetico, sem conexao\n" + "\n".join(old for old, _ in changes)
        if len(changes) < 1 or any(not old or not new for old, new in changes):
            raise ValueError("Mapeamento de transformacao invalido.")
    print(f"Instalador {VERSION}: AUTOVERIFICACAO APROVADA. Nenhum arquivo alterado.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: instala retries de leitura no Drive")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return _self_test()
        count = install(apply=args.apply)
    except (OSError, ValueError, SyntaxError, RuntimeError):
        print("BLOQUEADO: arquivos Python divergentes; nenhum arquivo remoto alterado.")
        return 1
    if not count:
        print(f"Instalador {VERSION}: integracao ja aplicada. Nenhuma alteracao.")
    elif args.check:
        print(f"Instalador {VERSION}: compatibilidade APROVADA; arquivos a modificar: {count}.")
    else:
        print(f"Instalador {VERSION}: integracao aplicada em {count} arquivos; sem conexao ou publicacao.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
