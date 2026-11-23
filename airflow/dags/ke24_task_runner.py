from textwrap import dedent
import os
import subprocess
from time import perf_counter


PYTHON_KE24 = "/opt/ke24-venv/bin/python"
PROJECT_ROOT = "/opt/ke24"


def executar_modulo(modulo=None, descricao="", timeout=1200, codigo=None):

    if (modulo is None) == (codigo is None):
        raise ValueError("Informe somente modulo ou codigo.")

    comando = (
        [PYTHON_KE24, "-m", modulo]
        if modulo is not None
        else [PYTHON_KE24, "-c", dedent(codigo).strip()]
    )

    print(f"[INICIO] {descricao}")
    inicio = perf_counter()

    try:
        resultado = subprocess.run(
            comando,
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "PYTHONPATH": PROJECT_ROOT,
            },
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        duracao = perf_counter() - inicio

        print(f"Duracao: {duracao:.2f}s")
        print(f"Codigo de retorno: {resultado.returncode}")

        if resultado.returncode != 0:
            raise RuntimeError(
                f"Falha na etapa {descricao}. "
                "Consulte a execucao local para diagnostico."
            )

        print(f"[OK] {descricao}")

    except subprocess.TimeoutExpired as erro:
        duracao = perf_counter() - inicio

        print(f"Duracao: {duracao:.2f}s")
        print(f"[TIMEOUT] {descricao}")

        raise RuntimeError(
            f"Tempo limite excedido: {descricao}"
        ) from erro
