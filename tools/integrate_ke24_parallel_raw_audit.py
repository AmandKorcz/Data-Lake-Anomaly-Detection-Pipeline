from __future__ import annotations

import argparse
import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "pipeline" / "run_ke24.py"
MARKER = "KE24_RAW_PARALLEL_AUDIT_V1"
INSTALLER_VERSION = "4.0.2"

OLD_BLOCK = '''    raw_tokens = set()

    for source in source_files:
        with download(service, source["id"]) as buffer:
            digest = calculate_hash(buffer)
        protected_hash = tokenize(
            value=digest,
            column=FILE_ID,
            company=None,
            secret_key=secret_key,
        )
        if protected_hash in raw_tokens:
            raise PreviewBlocked("Conteudo XLSX duplicado no raw; revisao necessaria.")
        raw_tokens.add(protected_hash)
'''

NEW_BLOCK = '''    # KE24_RAW_PARALLEL_AUDIT_V1: usa verificacao estrita em paralelo apenas
    # no caminho real. Testes com dependencias injetadas seguem sequenciais.
    if use_parallel:
        from src.pipeline.ke24_parallel_raw_audit import (
            DuplicateRawContent, audit_raw_sources_parallel,
        )
        try:
            raw_tokens = audit_raw_sources_parallel(source_files, secret_key)
        except DuplicateRawContent:
            raise PreviewBlocked(
                "Conteudo XLSX duplicado no raw; revisao necessaria."
            ) from None
    else:
        raw_tokens = set()
        for source in source_files:
            with download(service, source["id"]) as buffer:
                digest = calculate_hash(buffer)
            protected_hash = tokenize(
                value=digest,
                column=FILE_ID,
                company=None,
                secret_key=secret_key,
            )
            if protected_hash in raw_tokens:
                raise PreviewBlocked("Conteudo XLSX duplicado no raw; revisao necessaria.")
            raw_tokens.add(protected_hash)
'''

ORIGINAL_ENTRY = '    if list_raw is None or download is None:\n'
PARALLEL_ENTRY = '''    use_parallel = (
        list_raw is None and download is None and read_manifest is None
        and calculate_hash is None and tokenize is None
    )
    if list_raw is None or download is None:
'''


def _has_complete_integration(text):
    tree = ast.parse(text)
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "audit_drive_sources"
    ]
    if len(functions) != 1:
        return False
    body = functions[0].body

    expected_assignment = ast.parse(
        "def _expected():\n" + PARALLEL_ENTRY + "        pass\n"
    ).body[0].body[0]
    expected_parallel_if = ast.parse(
        "def _expected():\n" + NEW_BLOCK
    ).body[0].body[0]

    assignments = [
        node for node in body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "use_parallel"
                for target in node.targets)
    ]
    parallel_branches = [
        node for node in body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "use_parallel"
    ]
    dump = lambda node: ast.dump(node, include_attributes=False)
    return (
        len(assignments) == 1
        and len(parallel_branches) == 1
        and dump(assignments[0]) == dump(expected_assignment)
        and dump(parallel_branches[0]) == dump(expected_parallel_if)
        and text.count("    missing = raw_tokens - committed\n") == 1
        and OLD_BLOCK not in text
    )


def transform_source(text):
    if _has_complete_integration(text):
        return text, False

    if (
        MARKER in text
        or "use_parallel = (" in text
        or "audit_raw_sources_parallel(" in text
        or text.count(ORIGINAL_ENTRY) != 1
        or text.count(OLD_BLOCK) != 1
    ):
        raise ValueError("Versao do run_ke24.py divergente ou integracao parcial. Nenhuma alteracao aplicada.")

    updated = text.replace(ORIGINAL_ENTRY, PARALLEL_ENTRY, 1)
    updated = updated.replace(OLD_BLOCK, NEW_BLOCK, 1)
    if updated.count(MARKER) != 1 or not _has_complete_integration(updated):
        raise ValueError("A transformacao nao gerou a integracao completa. Nada foi alterado.")
    return updated, True


def install(path=TARGET, *, apply=False):
    path = Path(path)
    original = path.read_text(encoding="utf-8")
    updated, changed = transform_source(original)
    if not apply or not changed:
        return changed
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".ke24-raw-audit-", suffix=".tmp", delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(updated)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return changed


def _self_test():
    fixture = (
        "def audit_drive_sources(service, secret_key, *, list_raw=None, download=None, "
        "read_manifest=None, calculate_hash=None, tokenize=None):\n"
        + ORIGINAL_ENTRY
        + "        list_raw = list_raw or object()\n"
        + "        download = download or object()\n"
        + "    source_files = []\n"
        + "    committed = set()\n"
        + OLD_BLOCK
        + "    missing = raw_tokens - committed\n"
        + "    return missing\n"
    )
    transformed, changed = transform_source(fixture)
    again, second_change = transform_source(transformed)
    missing_marker = transformed.replace(
        "    # KE24_RAW_PARALLEL_AUDIT_V1: usa verificacao estrita em paralelo apenas\n"
        "    # no caminho real. Testes com dependencias injetadas seguem sequenciais.\n", ""
    )
    accepted, accepted_change = transform_source(missing_marker)
    if not changed or second_change or again != transformed or accepted_change or accepted != missing_marker:
        raise ValueError("Autoverificacao de idempotencia reprovada.")
    print(f"Instalador {INSTALLER_VERSION}: AUTOVERIFICACAO APROVADA. Nenhum arquivo alterado.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Integra auditoria raw paralela estrita")
    flags = parser.add_mutually_exclusive_group(required=True)
    flags.add_argument("--self-test", action="store_true", help="Teste local, sem alterar arquivos")
    flags.add_argument("--check", action="store_true")
    flags.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return _self_test()
        changes = install(apply=args.apply)
    except (OSError, SyntaxError, ValueError):
        print("BLOQUEADO: codigo incompatível ou arquivo indisponivel. Nao sobrescrito.")
        return 1
    if not changes:
        print(f"Instalador {INSTALLER_VERSION}: integracao ja aplicada. Nenhuma alteracao realizada.")
    elif args.check:
        print(f"Instalador {INSTALLER_VERSION}: compatibilidade APROVADA. Nenhum arquivo alterado.")
    else:
        print(f"Instalador {INSTALLER_VERSION}: integracao aplicada em run_ke24.py. Nenhuma conexao ou publicacao.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
