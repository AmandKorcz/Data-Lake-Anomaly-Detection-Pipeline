from __future__ import annotations

import argparse
import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "KE24_PROCESSED_SINGLE_PASS_V1"
VERSION = "6.2.0"

FEATURE_PATH = "src/pipeline/run_ke24_drive_features.py"
TEST_PATH = "tests/test_ke24_drive_features.py"

FEATURE_TRANSFORMS = (
    (
        "    load_processed_manifest,\n",
        "",
    ),
    (
        '    # Esta função já verifica a integridade e os arquivos esperados na pasta.\n'
        '    registered = load_processed_manifest(service)\n'
        '    if registered.empty:\n'
        '        raise RuntimeError("Nenhum lote processed publicado no Google Drive.")\n'
        '\n'
        '    files = list_processed_files(service)\n',
        '    # Verifica cada Parquet uma unica vez e usa esses MESMOS bytes para leitura.\n'
        '    files = list_processed_files(service)\n'
        '    if not files:\n'
        '        raise RuntimeError("Nenhum lote processed publicado no Google Drive.")\n',
    ),
    (
        '    if _manifest_signature(documented) != _manifest_signature(registered):\n'
        '        raise RuntimeError("Manifesto foi alterado durante a leitura; execução bloqueada.")\n'
        '\n',
        '',
    ),
    (
        '    if set(remote) != expected_ids:\n',
        '    if set(remote) != expected_ids or len(remote) != len(files):\n',
    ),
    (
        '    if _manifest_signature(actual_manifest) != _manifest_signature(registered):\n',
        '    if _manifest_signature(actual_manifest) != _manifest_signature(documented):\n',
    ),
    (
        '    return processed\n\n\ndef build_features_in_memory(',
        '    # Bloqueia alteracoes no manifesto ou na composicao da pasta durante a leitura.\n'
        '    # O reread e apenas do pequeno manifesto, nunca dos Parquets ja validados.\n'
        '    final_manifest = execute_drive_read(\n'
        '        lambda: service.files().get_media(fileId=manifest_files[0]["id"])\n'
        '    )\n'
        '    if final_manifest != raw_manifest:\n'
        '        raise RuntimeError("Manifesto foi alterado durante a leitura; execução bloqueada.")\n'
        '\n'
        '    final_files = list_processed_files(service)\n'
        '    if sorted((item["id"], item["name"]) for item in final_files) != sorted(\n'
        '        (item["id"], item["name"]) for item in files\n'
        '    ):\n'
        '        raise RuntimeError("Pasta processed alterada durante a leitura; execução bloqueada.")\n'
        '\n'
        '    return processed\n\n\ndef build_features_in_memory(',
    ),
)

TEST_TRANSFORMS = (
    (
        '    monkeypatch.setattr(\n'
        '        phase2,\n'
        '        "load_processed_manifest",\n'
        '        lambda service: pd.DataFrame(),\n'
        '    )\n',
        '    monkeypatch.setattr(phase2, "list_processed_files", lambda service: [])\n',
    ),
    (
        '    monkeypatch.setattr(\n'
        '        phase2, "load_processed_manifest", lambda service: registered\n'
        '    )\n',
        '',
    ),
)


def _transform(original: str, transforms: tuple[tuple[str, str], ...], *, feature: bool) -> tuple[str, bool]:
    if not isinstance(original, str):
        raise ValueError("Arquivo de codigo invalido.")
    if original.count(MARKER) > 1:
        raise ValueError("Marcador duplicado: revisar integracao manualmente.")
    old_counts = [original.count(old) for old, _ in transforms]
    new_counts = [original.count(new) for _, new in transforms if new]
    if MARKER in original:
        if any(old_counts[:-1] if feature else old_counts) or any(count != 1 for count in new_counts):
            raise ValueError("Integracao incompleta ou divergente.")
        ast.parse(original)
        return original, False
    if any(count != 1 for count in old_counts):
        raise ValueError("Codigo divergente da branch de referencia; nenhuma alteracao aplicada.")
    if any(new_counts):
        raise ValueError("Integracao parcial encontrada: revisar antes de continuar.")
    updated = original
    for old, new in transforms:
        updated = updated.replace(old, new, 1)
    if feature:
        prefix = f"# {MARKER}: verificacao e uso dos mesmos bytes Parquet.\n"
    else:
        prefix = f"# {MARKER}: fixtures de leitura unica.\n"
    updated = prefix + updated
    ast.parse(updated)
    if any(old in updated for old, _ in transforms[:-1] if feature) or (
        not feature and any(old in updated for old, _ in transforms)
    ):
        raise ValueError("Bloco antigo ainda presente, integracao bloqueada.")
    if any(updated.count(new) != 1 for _, new in transforms if new):
        raise ValueError("Bloco novo incorreto, integracao bloqueada.")
    if feature:
        tree = ast.parse(updated)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "load_verified_processed_dataframe"]
        if len(functions) != 1:
            raise ValueError("Assinatura de funcao inesperada.")
        if "load_processed_manifest(" in updated:
            raise ValueError("Ainda existe chamada de leitura duplicada.")
        if "hashlib.sha256(payload).hexdigest()" not in updated or "validate_protected_dataset(" not in updated:
            raise ValueError("Validacao de integridade ausente.")
    return updated, True


def transform_feature(source):
    return _transform(source, FEATURE_TRANSFORMS, feature=True)


def transform_test(source):
    return _transform(source, TEST_TRANSFORMS, feature=False)


def plan(root=ROOT):
    root = Path(root)
    staged = {}
    for relative, transform in ((FEATURE_PATH, transform_feature), (TEST_PATH, transform_test)):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError("Arquivo de integracao nao encontrado.")
        current = path.read_text(encoding="utf-8")
        updated, modified = transform(current)
        staged[path] = (current, updated, modified)
    return staged


def install(root=ROOT, *, apply=False):
    staged = plan(root)  # Checa os dois arquivos antes de qualquer gravacao.
    changed = [path for path, (_, _, modified) in staged.items() if modified]
    if not apply or not changed:
        return len(changed)
    written = []
    try:
        for path in changed:
            previous, updated, _ = staged[path]
            if path.read_text(encoding="utf-8") != previous:
                raise RuntimeError("Arquivo alterado apos validacao.")
            temp = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                    prefix=".ke24-single-pass-", suffix=".tmp", delete=False,
                ) as fh:
                    temp = Path(fh.name)
                    fh.write(updated)
                os.replace(temp, path)
                written.append((path, previous))
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
    except BaseException:
        for path, previous in reversed(written):
            path.write_text(previous, encoding="utf-8", newline="\n")
        raise
    return len(changed)


def _self_test():
    if len(FEATURE_TRANSFORMS) != 6 or len(TEST_TRANSFORMS) != 2:
        raise ValueError("Contrato de integracao invalido.")
    for old, new in (*FEATURE_TRANSFORMS, *TEST_TRANSFORMS):
        if not old or old == new:
            raise ValueError("Bloco de migracao invalido.")
    print(f"Instalador {VERSION}: autoverificacao APROVADA, sem alteracoes.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: leitura verificada de Parquet em uma passagem")
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--self-test", action="store_true")
    options.add_argument("--check", action="store_true")
    options.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return _self_test()
        count = install(apply=args.apply)
    except (OSError, ValueError, SyntaxError, RuntimeError):
        print("BLOQUEADO: arquivos divergentes; nenhuma alteracao remota realizada.")
        return 1
    if not count:
        print(f"Instalador {VERSION}: integracao ja aplicada, nenhuma alteracao.")
    elif args.check:
        print(f"Instalador {VERSION}: compatibilidade APROVADA; arquivos a modificar: {count}.")
    else:
        print(f"Instalador {VERSION}: integracao aplicada em {count} arquivos; sem conexoes remotas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
