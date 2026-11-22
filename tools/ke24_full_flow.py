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

class FlowStopped(Exception):
    """Interromper sem repeticao automatica."""

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

SAFE_LINE = re.compile(
    r"^(?:Processando extra[cç][aã]o SAP KE24|"
    r"Manifesto verificado: \d+ extra[cç][oõ]es protegidas\.|"
    r"Arquivos novos: \d+|Extracoes novas: \d+|Registros novos: \d+|"
    r"Registros de treino: \d+|Grupos empresa/moeda treinados: \d+|"
    r"Candidatos p95 \(sinais estatisticos\): \d+|Candidatos estaveis nas 5 seeds: \d+|Alertas novos: \d+|"
    r"Candidatos que exigem revisao antes de publicar: \d+|"
    r"Total de alertas no historico: \d+|"
    r"(?:existing_alerts|new_alerts|queue_alerts|context_existing|context_new|requests_not_authenticated): \d+|"
    r"published: (?:True|False)|"
    r"Status: (?:APROVADO|PREVIA APROVADA\.|PUBLICACAO VERIFICADA \(\d+ alertas no historico\)\.))$"
)

def run_module(label: str, module: str, args: tuple[str, ...] = (), *, verbose=False) -> Result:
    print(f"\n▶ {label}", flush=True)
    print("   Executando com compatibilidade IPv4...", flush=True)
    start = time.monotonic()
    command = [sys.executable, "-m", IPV4, module, *args]
    try:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        raise FlowStopped(f"Falha ao iniciar comando: {type(exc).__name__}") from None
    lines = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            msg = line.strip()
            if verbose or SAFE_LINE.fullmatch(msg):
                print("   " + msg, flush=True)
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
    print(f"   {'✓ CONCLUIDO' if code == 0 else '✗ FALHOU'} ({time.monotonic()-start:.1f}s)", flush=True)
    return Result(code, "".join(lines))


def number(output: str, label: str) -> int:
    found = re.findall(r"^" + re.escape(label) + r":[ \t]*(\d+)[ \t]*$", output, flags=re.M)
    if not found or len(set(found)) != 1:
        raise FlowStopped(f"Contagem ausente ou divergente na etapa: {label}.")
    return int(found[0])


def approve(stage: str, ask: Callable[[str], str]) -> bool:
    print(f"\n⚠ PUBLICACAO REAL: {stage}")
    print("   Esta confirmacao vale somente para esta etapa.")
    try:
        return ask("   Digite PUBLICAR para autorizar (qualquer outra coisa cancela): ").strip() == "PUBLICAR"
    except (KeyboardInterrupt, EOFError):
        return False


def execute(*, guided: bool = False, runner=run_module, ask=input,
            verbose: bool = False) -> Summary:
    writes: list[str] = []
    alerts_total: int | None = None
    print("\nKE24 | FLUXO INTEGRADO", "ASSISTIDO" if guided else "PREVIA SEM GRAVACAO", sep=" — ")

    def step(label, module, *args, requires=None):
        result = runner(label, module, tuple(args), verbose=verbose)
        if result.code:
            raise FlowStopped(f"{label}: falha do modulo (codigo {result.code}). "
                              "Nao repetir publicacoes sem verificar o estado remoto.")
        if requires and requires not in result.output:
            raise FlowStopped(f"{label}: confirmacao de seguranca nao encontrada. "
                              "Se houve escrita, confira o Drive/Sheets antes de repetir.")
        return result.output

    def paused(phase):
        print(f"\n⏸ PAUSADO em {phase}. Para retomar, rode --guided novamente.")
        print("   Cada execucao revalida o estado atual antes de qualquer nova escrita.")
        return Summary(False, phase, tuple(writes), alerts_total)

    pre = step("1/7 Validacao raw", PRE, requires="Preflight E2E: APROVADO")
    incoming = number(pre, "Extracoes novas")
    ing = step("2/7 Previa da ingestao processed", ING)
    if number(ing, "Arquivos novos") != incoming:
        raise FlowStopped("raw mudou entre preflight e previa: revalidar.")
    if incoming:
        if "Status: APROVADO" not in ing:
            raise FlowStopped("Previa da ingestao sem aprovacao.")
        if not guided or not approve("incluir XLSX novos no processed", ask):
            return paused("processed")
        written = step("2/7 Publicando processed", ING, "--publish-incremental",
                       requires="Lote incremental publicado e validado.")
        if "Status: APROVADO" not in written:
            raise FlowStopped("Publicacao processed nao confirmada. NAO repetir sem conferir.")
        writes.append("processed")
        checked = step("2/7 Conferindo idempotencia", ING)
        if number(checked, "Arquivos novos") != 0:
            raise FlowStopped("Idempotencia processed nao confirmada.")
    else:
        print("   ✓ Sem arquivos novos; processed mantido.")

    step("3/7 Treino e inferencia temporal (leitura)", MODEL, requires="Status: APROVADO")
    preview = step("4/7 Previa de alertas", ALERT,
                   requires="Status: PREVIA APROVADA. Nenhum upload realizado.")
    blockers = number(preview, "Candidatos que exigem revisao antes de publicar")
    new_alerts = number(preview, "Alertas novos")
    alerts_total = number(preview, "Total de alertas no historico")
    if blockers:
        raise FlowStopped(f"{blockers} recorrencias ambiguas: publicacao BLOQUEADA.")
    if alerts_total > MAX_REVIEW_ALERTS:
        raise FlowStopped(f"Historico projetado ({alerts_total}) excede a capacidade "
                          f"de {MAX_REVIEW_ALERTS} da fila/contexto. "
                          "Redimensione e valide ambos antes de publicar alertas.")
    if new_alerts:
        if not guided or not approve("atualizar historico protegido de alertas", ask):
            return paused("historico de alertas")
        confirmed = f"Status: PUBLICACAO VERIFICADA ({alerts_total} alertas no historico)."
        step("4/7 Publicando historico protegido", ALERT, "--publish", requires=confirmed)
        writes.append("alert_history")
    else:
        print("   ✓ Nenhum alerta novo.")

    queue = step("5/7 Previa da fila Google Sheets", QUEUE, "--preview", requires="published: False")
    que_old, que_new = number(queue, "existing_alerts"), number(queue, "new_alerts")
    pending = number(queue, "requests_not_authenticated")
    if que_old + que_new != alerts_total:
        raise FlowStopped("Historico e fila discordam em quantidade.")
    if pending:
        print(f"   ℹ {pending} pedido(s) de revisao nao autenticados; nenhuma aprovacao automatica.")
    if que_new:
        if not guided or not approve("adicionar novas linhas na fila de revisao", ask):
            return paused("fila de revisao")
        step("5/7 Publicando fila", QUEUE, "--publish", "--confirm-publish",
             requires="published: True")
        writes.append("review_queue")
        check = step("5/7 Conferindo fila e snapshot", QUEUE, "--preview", requires="published: False")
        if number(check, "new_alerts") or number(check, "existing_alerts") != alerts_total:
            raise FlowStopped("Fila nao sincronizada apos escrita: NAO repetir sem conferir.")
    else:
        print("   ✓ Fila ja sincronizada.")

    ctx = step("6/7 Previa de contexto", CONTEXT, "--preview", requires="published: False")
    ctx_old, ctx_new = number(ctx, "context_existing"), number(ctx, "context_new")
    if number(ctx, "queue_alerts") != alerts_total or ctx_old + ctx_new != alerts_total:
        raise FlowStopped("Contexto e fila divergem.")
    if ctx_new:
        if not guided or not approve("adicionar novas linhas no contexto", ask):
            return paused("contexto")
        step("6/7 Publicando contexto", CONTEXT, "--publish", "--confirm-publish",
             requires="published: True")
        writes.append("review_context")
        check = step("6/7 Conferindo contexto", CONTEXT, "--preview", requires="published: False")
        if number(check, "context_new") or number(check, "context_existing") != alerts_total:
            raise FlowStopped("Contexto nao sincronizado apos escrita: NAO repetir sem conferir.")
    else:
        print("   ✓ Contexto ja sincronizado.")

    print(f"\n✓ 7/7 CONCLUIDO: {alerts_total} alertas consistentes no historico, fila e contexto.")
    print("   Looker Studio usa Sheets: confira a atualizacao visual no relatorio.")
    print("   Sinal estatistico nao comprova erro financeiro.")
    return Summary(True, None, tuple(writes), alerts_total)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Orquestracao serial KE24 com confirmacao por escrita")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="Padrao: nao publica nada")
    modes.add_argument("--guided", action="store_true", help="Executa publicacoes confirmadas uma a uma")
    parser.add_argument("--verbose", action="store_true", help="Exibe stdout integral; pode conter metadados privados")
    args = parser.parse_args(argv)
    try:
        report = execute(guided=args.guided, verbose=args.verbose)
    except FlowStopped as exc:
        print("\n✗ INTERROMPIDO COM SEGURANCA:", exc)
        return 1
    except KeyboardInterrupt:
        print("\n✗ Interrompido. Se houve escrita, confira o remoto antes de repetir.")
        return 130
    return 0 if report.completed or not args.guided else 2

if __name__ == "__main__":
    raise SystemExit(main())
