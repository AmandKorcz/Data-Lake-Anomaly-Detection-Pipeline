from textwrap import dedent
import os
import signal
import subprocess
from time import perf_counter

import re

PYTHON_KE24 = "/opt/ke24-venv/bin/python"
PROJECT_ROOT = "/opt/ke24"


def executar_modulo(modulo=None, descricao="", timeout=1200, codigo=None, argumentos=()):

    if (modulo is None) == (codigo is None):
        raise ValueError("Informe somente modulo ou codigo.")

    comando = (
        [PYTHON_KE24, "-m", modulo, *argumentos]
        if modulo is not None
        else [PYTHON_KE24, "-c", dedent(codigo).strip()]
    )

    print(f"[INICIO] {descricao}")
    inicio = perf_counter()

    try:
        with subprocess.Popen(
            comando,
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "PYTHONPATH": PROJECT_ROOT,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as processo:
            try:
                stdout, stderr = processo.communicate(timeout=timeout)
            except BaseException:
                # O fluxo serial abre subprocessos que tambem devem parar.
                try:
                    os.killpg(processo.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise

        duracao = perf_counter() - inicio

        print(f"Duracao: {duracao:.2f}s")
        print(f"Codigo de retorno: {processo.returncode}")

        if processo.returncode != 0:
            etapas = re.findall(
                r"(?m)^\[(\d{2}/\d{2})\]\s+([A-Z0-9 /()-]+)$",
                stdout or "",
            )

            if etapas:
                numero, nome = etapas[-1]
                print(f"Ultima etapa interna: {numero} {nome}")
            else:
                print("Ultima etapa interna: nao identificada")

            for nome, conteudo in (("stdout", stdout), ("stderr", stderr)):
                linhas = (conteudo or "").splitlines()
                if linhas:
                    print(f"Diagnostico interno {nome} (ultimas 80 linhas):")
                    print("\n".join(linhas[-80:]))

            raise RuntimeError(
                f"Falha na etapa {descricao}. "
                "Consulte o diagnostico da etapa interna."
            )

        print(f"[OK] {descricao}")
        return stdout

    except subprocess.TimeoutExpired:
        duracao = perf_counter() - inicio

        print(f"Duracao: {duracao:.2f}s")
        print(f"[TIMEOUT] {descricao}")

        raise RuntimeError(
            f"Tempo limite excedido: {descricao}"
        ) from None
