from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_IGNORED = (
    "config/ke24.keys.env",
    "config/example.keys.env",
    "config/example.local.env",
    "config/example.private.env",
    "config/.env",
    "oauth-client.json",
    "drive-token.json",
    "config/client_secret_example.json",
)

PRIVATE_EXACT_NAMES = {
    "token.json",
    "drive-token.json",
    "drive-write-token.json",
    "oauth-client.json",
}
PRIVATE_DIRECTORY_NAMES = {"credentials", "secrets"}
PRIVATE_SUFFIXES = (".keys.env", ".local.env", ".private.env", ".pem", ".key")


class RepositoryGuardError(RuntimeError):
    """Nao foi possivel confirmar a politica do indice Git."""


@dataclass(frozen=True)
class RepositoryGuardResult:
    tracked_private_count: int
    missing_ignore_count: int

    @property
    def approved(self) -> bool:
        return self.tracked_private_count == 0 and self.missing_ignore_count == 0


def is_private_path(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    parts = path.replace("\\", "/").strip("/").casefold().split("/")
    if any(part in PRIVATE_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    filename = parts[-1]
    if filename in PRIVATE_EXACT_NAMES:
        return True
    if filename == ".env" or filename.startswith(".env."):
        return True
    if filename.endswith(PRIVATE_SUFFIXES):
        return True
    if filename.startswith("client_secret") and filename.endswith(".json"):
        return True
    if filename.startswith("service-account") and filename.endswith(".json"):
        return True
    if filename.startswith("credentials") and filename.endswith(".json"):
        return True
    return False


def _git(root: Path, *args: str, capture_output=True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        raise RepositoryGuardError("Nao foi possivel consultar o Git.") from None
    return proc


def indexed_paths(root: Path) -> tuple[str, ...]:
    proc = _git(root, "ls-files", "--cached", "-z")
    if proc.returncode != 0:
        raise RepositoryGuardError("Indice Git indisponivel.")
    return tuple(
        value.decode("utf-8", errors="replace")
        for value in proc.stdout.split(b"\x00") if value
    )


def is_ignored(root: Path, relative_path: str) -> bool:
    proc = _git(root, "check-ignore", "--quiet", "--no-index", "--", relative_path,
                capture_output=False)
    if proc.returncode not in (0, 1):
        raise RepositoryGuardError("Nao foi possivel validar o .gitignore.")
    return proc.returncode == 0


def check_repository(root: Path = PROJECT_ROOT) -> RepositoryGuardResult:
    root = Path(root)
    private_count = sum(is_private_path(path) for path in indexed_paths(root))
    missing_count = sum(not is_ignored(root, name) for name in EXPECTED_IGNORED)
    return RepositoryGuardResult(private_count, missing_count)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Auditoria offline de arquivos locais do KE24")
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        result = check_repository()
    except RepositoryGuardError:
        print("BLOQUEADO: nao foi possivel verificar a configuracao do Git.")
        return 1
    if not result.approved:
        print(
            "BLOQUEADO: arquivos privados no indice=%d; regras de ignore ausentes=%d."
            % (result.tracked_private_count, result.missing_ignore_count)
        )
        print("Nenhum arquivo foi alterado. Verifique o indice e o .gitignore localmente.")
        return 1
    print("APROVADO: nenhum arquivo privado rastreado; regras de ignore verificadas.")
    print("Verificacao offline, sem conexoes ou alteracoes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
