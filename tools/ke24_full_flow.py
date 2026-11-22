from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
IPV4 = "tools.run_ke24_ipv4"
PRE = "tools.ke24_new_company_preflight"
ING = "src.pipeline.run_ke24_drive_ingestion"
MODEL = "src.pipeline.run_ke24_drive_model"
ALERT = "src.pipeline.run_ke24_drive_alert_persistence"
QUEUE = "src.review.run_ke24_review_sheet"
CONTEXT = "tools.ke24_review_context_tab"
MAX_REVIEW_ALERTS = 5000

SEPARATOR = "-" * 68


def stage_header(label: str) -> None:
    number, _, description = label.partition(" ")
    if "/" in number:
        current, total = number.split("/", 1)
        if current.isdigit() and total.isdigit():
            number = f"{int(current):02d}/{int(total):02d}"
    print(f"\n{SEPARATOR}\n[{number}] {description.upper()}", flush=True)


def elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(round(seconds)), 60)
    return f"{minutes}min {seconds:02d}s"


def quantity(value: int) -> str:
    return f"{value:,}".replace(",", ".")


class FlowStopped(Exception):
    """O pipeline nao deve prosseguir apos resultado incerto."""


@dataclass(frozen=True)
class Result:
    code: int
    output: str


@dataclass(frozen=True)
class Summary:
    completed: bool
    paused_at: str | None
    writes: tuple[str, ...]
    alerts: int | None


def run_module(
    label: str,
    module: str,
    args: tuple[str, ...] = (),
    *,
    verbose: bool = False,
) -> Result:
    print("   Processando...", flush=True)
    start = time.monotonic()
    command = [sys.executable, "-m", IPV4, module, *args]

    try:
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise FlowStopped(f"Falha ao iniciar comando: {type(exc).__name__}") from None

    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if verbose:
                print("      " + line.rstrip("\n"), flush=True)
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()

    status = "OK" if code == 0 else "FALHOU"
    print(f"   {status} | Tempo: {elapsed(time.monotonic() - start)}", flush=True)
    return Result(code, "".join(lines))


def number(output: str, label: str) -> int:
    found = re.findall(
        r"^\s*" + re.escape(label) + r":\s*(\d+)\s*$",
        output,
        flags=re.M,
    )
    if not found or len(set(found)) != 1:
        raise FlowStopped(f"Contagem ausente ou divergente na etapa: {label}.")
    return int(found[0])


def approve(stage: str, ask: Callable[[str], str]) -> bool:
    print(f"\n{SEPARATOR}")
    print("CONFIRMACAO DE PUBLICACAO")
    print(f"   Operacao: {stage}")
    print("   Os dados serao gravados somente com sua autorizacao.")
    try:
        return ask("   Digite PUBLICAR para confirmar (ou Enter para cancelar): ").strip() == "PUBLICAR"
    except (KeyboardInterrupt, EOFError):
        return False


def execute(
    *,
    guided: bool = False,
    runner=run_module,
    ask=input,
    verbose: bool = False,
    fast: bool = False,
    auto: bool = False,
) -> Summary:
    """Executa o fluxo; auto escolhe fast ou completo apos o preflight."""
    writes: list[str] = []
    alerts_total: int | None = None
    start = time.monotonic()

    mode = "Assistido (confirma publicacoes)" if guided else "Previa (somente leitura)"
    print(f"\n{SEPARATOR}")
    print("KE24  |  PIPELINE DE DETECCAO DE ANOMALIAS")
    print(f"Modo: {mode}")
    print(f"Selecao de fluxo: {'automatica' if auto else 'rapida manual' if fast else 'completa'}")
    print(SEPARATOR)
    pending_reviews = 0
    new_alerts = 0



    def step(label: str, module: str, *args: str, requires: str | None = None) -> str:
        stage_header(label)
        result = runner(label, module, tuple(args), verbose=verbose)
        if result.code:
            raise FlowStopped(
                f"{label}: falha do modulo (codigo {result.code}). "
                "Nao repetir publicacoes sem verificar o estado remoto."
            )
        if requires and requires not in result.output:
            raise FlowStopped(
                f"{label}: confirmacao esperada nao encontrada. "
                "Se houve escrita, confira Drive/Sheets antes de repetir."
            )
        return result.output

    def paused(phase: str) -> Summary:
        command = "python -m tools.ke24_full_flow --guided"
        if fast and not auto:
            command += " --fast"
        print(f"\n{SEPARATOR}")
        print(f"PAUSADO | Etapa: {phase}")
        print(f"   Retomar: {command}")
        print("   O estado remoto sera validado novamente antes da gravacao.")
        print(SEPARATOR)
        return Summary(False, phase, tuple(writes), alerts_total)

    # 1. Preflight segue SEMPRE completo, inclusive em --fast.
    pre = step("1/7 Validacao raw", PRE, requires="Preflight E2E: APROVADO")
    incoming = number(pre, "Extracoes novas")
    new_rows = number(pre, "Registros novos")
    print(f"   Novas extracoes: {quantity(incoming)} | Novos registros: {quantity(new_rows)}")

    # So a auditoria completa aprovada pode decidir qual caminho seguir.
    # Nao usar apenas a lista de nomes/IDs do Drive para concluir que nao ha
    # novos dados: hashes protegidos e manifesto precisam continuar verificados.
    if auto:
        if (incoming == 0) != (new_rows == 0):
            raise FlowStopped(
                "Preflight com contagens inconsistentes de extracoes e registros. "
                "Nenhuma etapa seguinte executada."
            )
        fast = incoming == 0
        if fast:
            print("   Fluxo selecionado: RAPIDO (nenhum arquivo novo)")
        else:
            print(f"   Fluxo selecionado: COMPLETO ({quantity(incoming)} nova(s) extracao(oes))")

    # 2. Nao ha motivo para baixar todos os XLSX de novo se a etapa 1 confirmou
    # que o conjunto de extracoes ja corresponde ao manifesto.
    if fast and incoming == 0 and new_rows == 0:
        stage_header("2/7 Ingestao - processed")
        print("   Dispensada | Nenhum arquivo novo para processar.")
    else:
        ing = step("2/7 Previa da ingestao processed", ING)
        if number(ing, "Arquivos novos") != incoming:
            raise FlowStopped("RAW mudou entre preflight e previa: revalidar.")
        if incoming:
            if "Status: APROVADO" not in ing:
                raise FlowStopped("Previa da ingestao sem aprovacao.")
            if not guided or not approve("incluir XLSX novos no processed", ask):
                return paused("processed")
            written = step(
                "2/7 Publicando processed", ING, "--publish-incremental",
                requires="Lote incremental publicado e validado.",
            )
            if "Status: APROVADO" not in written:
                raise FlowStopped("Publicacao processed nao confirmada. NAO repetir sem conferir.")
            writes.append("processed")
            checked = step("2/7 Conferindo idempotencia", ING)
            if number(checked, "Arquivos novos") != 0:
                raise FlowStopped("Idempotencia processed nao confirmada.")
        else:
            print("   Nenhum arquivo novo | Processed mantido.")

    # 3. O algoritmo ja e executado dentro da etapa 4 (build_alert_preview_in_memory).
    if fast:
        stage_header("3/7 Machine Learning")
        print("   Integrado a etapa de alertas | Sem execucao duplicada.")
    else:
        step("3/7 Treino e inferencia temporal (leitura)", MODEL,
             requires="Status: APROVADO")

    # 4. Esta etapa continua executando o modelo e verificando recorrencia real.
    preview = step(
        "4/7 Previa de alertas", ALERT,
        requires="Status: PREVIA APROVADA. Nenhum upload realizado.",
    )
    blockers = number(preview, "Candidatos que exigem revisao antes de publicar")
    new_alerts = number(preview, "Alertas novos")
    alerts_total = number(preview, "Total de alertas no historico")
    print(f"   Alertas novos: {quantity(new_alerts)} | Total no historico: {quantity(alerts_total)}")
    if blockers:
        raise FlowStopped(f"{blockers} recorrencias ambiguas: publicacao BLOQUEADA.")
    if alerts_total > MAX_REVIEW_ALERTS:
        raise FlowStopped(
            f"Historico projetado ({alerts_total}) excede capacidade de "
            f"{MAX_REVIEW_ALERTS} da fila/contexto."
        )
    if new_alerts:
        if not guided or not approve("atualizar historico protegido de alertas", ask):
            return paused("historico de alertas")
        confirmed = f"Status: PUBLICACAO VERIFICADA ({alerts_total} alertas no historico)."
        step("4/7 Publicando historico protegido", ALERT, "--publish", requires=confirmed)
        writes.append("alert_history")
    else:
        print("   Historico mantido | Sem novos alertas.")

    # 5. Fila de revisao sem sobrescrever classificacoes humanas.
    queue = step(
        "5/7 Previa da fila Google Sheets", QUEUE, "--preview",
        requires="published: False",
    )
    que_old = number(queue, "existing_alerts")
    que_new = number(queue, "new_alerts")
    pending = number(queue, "requests_not_authenticated")
    pending_reviews = pending
    print(f"   Na fila: {quantity(que_old)} | A adicionar: {quantity(que_new)}")
    if que_old + que_new != alerts_total:
        raise FlowStopped("Historico e fila discordam em quantidade.")
    # Pedidos humanos nao autenticados sao informados apenas no resumo.
    if que_new:
        if not guided or not approve("adicionar novas linhas na fila de revisao", ask):
            return paused("fila de revisao")
        step(
            "5/7 Publicando fila", QUEUE, "--publish", "--confirm-publish",
            requires="published: True",
        )
        writes.append("review_queue")
        check = step(
            "5/7 Conferindo fila e snapshot", QUEUE, "--preview",
            requires="published: False",
        )
        if number(check, "new_alerts") or number(check, "existing_alerts") != alerts_total:
            raise FlowStopped("Fila nao sincronizada apos escrita: NAO repetir sem conferir.")
    else:
        print("   Fila sincronizada.")

    # 6. Contexto de leitura, com comparacao explicita ao historico e a fila.
    ctx = step("6/7 Previa de contexto", CONTEXT, "--preview", requires="published: False")
    ctx_old = number(ctx, "context_existing")
    ctx_new = number(ctx, "context_new")
    print(f"   Registros existentes: {quantity(ctx_old)} | A adicionar: {quantity(ctx_new)}")
    if number(ctx, "queue_alerts") != alerts_total or ctx_old + ctx_new != alerts_total:
        raise FlowStopped("Contexto e fila divergem.")
    if ctx_new:
        if not guided or not approve("adicionar novas linhas no contexto", ask):
            return paused("contexto")
        step(
            "6/7 Publicando contexto", CONTEXT, "--publish", "--confirm-publish",
            requires="published: True",
        )
        writes.append("review_context")
        check = step(
            "6/7 Conferindo contexto", CONTEXT, "--preview",
            requires="published: False",
        )
        if number(check, "context_new") or number(check, "context_existing") != alerts_total:
            raise FlowStopped("Contexto nao sincronizado apos escrita: NAO repetir sem conferir.")
    else:
        print("   Contexto sincronizado.")

    stage_header("7/7 Resumo da execucao")
    print("   Status: CONCLUIDO")
    print(f"   Tipo de fluxo: {'RAPIDO' if fast else 'COMPLETO'}")
    print(f"   Novas extracoes: {quantity(incoming)} | Novos alertas: {quantity(new_alerts)}")
    print(f"   Historico / fila / contexto: {quantity(alerts_total)} alertas")
    print(f"   Publicacoes realizadas: {len(writes)}")
    if pending_reviews:
        print(f"   Revisoes nao autenticadas: {quantity(pending_reviews)} (nenhuma aprovada automaticamente)")
    print(f"   Tempo total: {elapsed(time.monotonic() - start)}")
    print("   Nota: sinais estatisticos exigem validacao financeira.")
    print(SEPARATOR)
    return Summary(True, None, tuple(writes), alerts_total)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Orquestracao serial KE24 com confirmacao por escrita"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="Selecao automatica, sem publicacoes (padrao)")
    modes.add_argument("--guided", action="store_true", help="Publicacoes confirmadas uma a uma")
    parser.add_argument(
        "--fast", action="store_true",
        help="Modo rapido manual anterior (opcional; padrao agora escolhe automaticamente)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Exibe stdout integral; pode conter metadados privados",
    )
    args = parser.parse_args(argv)
    try:
        report = execute(
            guided=args.guided,
            fast=args.fast,
            auto=not args.fast,
            verbose=args.verbose,
        )
    except FlowStopped as exc:
        print(f"\n{SEPARATOR}\nEXECUCAO INTERROMPIDA | {exc}")
        print("   Para investigar: execute novamente com --verbose (pode exibir dados privados).")
        print(SEPARATOR)
        return 1
    except KeyboardInterrupt:
        print(f"\n{SEPARATOR}\nEXECUCAO CANCELADA")
        print("   Se houve escrita, confira Drive/Sheets antes de repetir.")
        print(SEPARATOR)
        return 130
    return 0 if report.completed or not args.guided else 2


if __name__ == "__main__":
    raise SystemExit(main())
