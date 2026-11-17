import re

from src.loading.plan_ke24_incremental import FILE_ID

_PROTECTED_HASH = re.compile(r"TKN_[0-9a-f]{32}\Z")


def should_process_source(
    buffer,
    loaded_manifest,
    seen_tokens: set[str],
    secret_key: bytes,
    *,
    hash_function=None,
    token_function=None,
) -> bool:
    """Decide se um XLSX deve ser aberto pelo parser de ingestão.

    `buffer` contém os bytes de um arquivo recebido do Drive em memória.
    `seen_tokens` acompanha TODAS as origens examinadas nesta execução para
    bloquear cópias de conteúdo, mesmo quando já constam do manifesto.
    Retorna False para um arquivo já publicado, True para um arquivo novo.
    """
    if not isinstance(secret_key, bytes) or len(secret_key) < 32:
        raise ValueError("Chave de proteção de origem inválida.")
    if not isinstance(seen_tokens, set):
        raise TypeError("O controle de origens observadas deve ser um conjunto.")
    if FILE_ID not in loaded_manifest.columns or loaded_manifest.columns.duplicated().any():
        raise ValueError("Contrato do manifesto processed inválido.")

    known = loaded_manifest[FILE_ID]
    if known.isna().any():
        raise ValueError("Manifesto processed possui origem sem identificador.")
    committed = set(known.astype(str))
    if len(committed) != len(known) or not all(_PROTECTED_HASH.fullmatch(v) for v in committed):
        raise ValueError("Manifesto processed possui hashes protegidos inválidos ou duplicados.")

    if hash_function is None:
        from src.ingestion.prepare_ke24 import calculate_file_hash
        hash_function = calculate_file_hash
    if token_function is None:
        from src.privacy.anonymize_ke24 import generate_token
        token_function = generate_token

    raw_hash = hash_function(buffer)
    protected = token_function(
        value=raw_hash,
        column=FILE_ID,
        company=None,
        secret_key=secret_key,
    )
    if not isinstance(protected, str) or not _PROTECTED_HASH.fullmatch(protected):
        raise ValueError("Identificador de conteúdo não está protegido corretamente.")
    if protected in seen_tokens:
        raise ValueError("Extrações com conteúdo repetido na pasta raw; revisão necessária.")
    seen_tokens.add(protected)
    return protected not in committed


def collect_new_drive_frames(
    service,
    source_files,
    loaded_manifest,
    secret_key,
    *,
    expected_columns,
    privacy_policy,
    privacy_schema,
    download,
    parse,
    anonymize,
    validate,
    hash_function=None,
    token_function=None,
):
    """Lê o XLSX apenas quando sua origem ainda não está no manifesto.

    Os bytes chegam do Drive em memória. Todos os arquivos são verificados por
    hash protegido; somente os novos são interpretados pelo pandas. A função
    não publica nem grava artefatos. Retorna quadros protegidos e contagens.
    """
    if not source_files:
        raise ValueError("A pasta raw do Drive não possui extrações XLSX.")

    seen_tokens = set()
    new_frames = []
    skipped = 0

    for source in source_files:
        if not isinstance(source, dict) or not source.get("id") or not source.get("name"):
            raise ValueError("Metadados de extração do Drive inválidos.")

        with download(service, source["id"]) as buffer:
            before = seen_tokens.copy()
            is_new = should_process_source(
                buffer,
                loaded_manifest,
                seen_tokens,
                secret_key,
                hash_function=hash_function,
                token_function=token_function,
            )
            if not is_new:
                skipped += 1
                continue

            selected_token = next(iter(seen_tokens - before))
            df, *_ = parse(
                buffer,
                expected_columns=expected_columns,
                source_name=source["name"],
            )

        protected = anonymize(
            df=df,
            policy=privacy_policy,
            schema=privacy_schema,
            secret_key=secret_key,
        )
        validate(protected, privacy_policy, require_all_columns=True)
        if (
            protected.empty
            or FILE_ID not in protected.columns
            or protected[FILE_ID].isna().any()
            or set(protected[FILE_ID].astype(str)) != {selected_token}
        ):
            raise ValueError("Identificador da extração diverge dos bytes recebidos do Drive.")
        new_frames.append(protected)

    return new_frames, {
        "raw_files": len(source_files),
        "new_files": len(new_frames),
        "skipped_files": skipped,
    }
