import hashlib
import json
import os
import shutil
import tempfile

from contextlib import ExitStack, contextmanager
from pathlib import Path

import pandas as pd


STATE_FILE = "state.json"


def _paths(history_file, dashboard_file):
    history = Path(history_file).resolve()
    dashboard = Path(dashboard_file).resolve()

    if history == dashboard:
        raise ValueError(
            "History and dashboard paths must differ"
        )

    return history, dashboard


def _transaction_dir(history, dashboard):
    key = hashlib.sha256(
        (
            str(history) + "\n" + str(dashboard)
        ).encode("utf-8")
    ).hexdigest()[:24]

    return history.parent / f".ke24_txn_{key}"


def _fsync_directory(directory):
    if os.name == "nt":
        return

    descriptor = os.open(directory, os.O_RDONLY)

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _locked_file(path):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)

        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()

        stream.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    stream.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

        except OSError as error:
            raise RuntimeError(
                "Another KE24 output writer is active"
            ) from error

        try:
            yield
        finally:
            stream.seek(0)

            if os.name == "nt":
                msvcrt.locking(
                    stream.fileno(),
                    msvcrt.LK_UNLCK,
                    1,
                )
            else:
                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_UN,
                )


def _stage_csv(dataset, destination):
    descriptor, filename = tempfile.mkstemp(
        prefix=".ke24_stage_",
        suffix=".csv",
        dir=destination.parent,
    )

    staged = Path(filename)

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            dataset.to_csv(
                stream,
                index=False,
            )

            stream.flush()
            os.fsync(stream.fileno())

    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    return staged


def _copy_fsynced(source, destination):
    with (
        source.open("rb") as input_stream,
        destination.open("xb") as output_stream,
    ):
        shutil.copyfileobj(
            input_stream,
            output_stream,
        )

        output_stream.flush()
        os.fsync(output_stream.fileno())


def _hash_file(path):
    hasher = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            hasher.update(block)

    return hasher.hexdigest()


def _write_state(transaction, payload):
    temporary = transaction / ".state.tmp"

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            sort_keys=True,
        )

        stream.flush()
        os.fsync(stream.fileno())

    os.replace(
        temporary,
        transaction / STATE_FILE,
    )

    _fsync_directory(transaction)


def _recover_locked(history, dashboard):
    transaction = _transaction_dir(
        history,
        dashboard,
    )

    if not transaction.exists():
        return False

    if not transaction.is_dir():
        raise RuntimeError(
            "Invalid KE24 transaction location"
        )

    state_file = transaction / STATE_FILE

    if not state_file.is_file():
        # Publication cannot begin before the
        # PREPARED journal exists.
        shutil.rmtree(transaction)
        return True

    try:
        state = json.loads(
            state_file.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "Unverifiable KE24 transaction; "
            "manual reconciliation required"
        ) from error

    expected_keys = {
        "version",
        "status",
        "history",
        "dashboard",
        "existed",
        "backup_sha256",
    }

    if (
        not isinstance(state, dict)
        or set(state) != expected_keys
        or state["version"] != 1
        or state["status"] not in {
            "PREPARED",
            "COMMITTED",
        }
        or state["history"] != str(history)
        or state["dashboard"] != str(dashboard)
        or not isinstance(
            state["existed"],
            dict,
        )
        or not isinstance(
            state["backup_sha256"],
            dict,
        )
        or set(state["existed"]) != {
            "history",
            "dashboard",
        }
        or set(state["backup_sha256"]) != {
            "history",
            "dashboard",
        }
        or any(
            type(value) is not bool
            for value in state["existed"].values()
        )
    ):
        raise RuntimeError(
            "Unverifiable KE24 transaction; "
            "manual reconciliation required"
        )

    if state["status"] == "PREPARED":
        # Verify all backups before touching outputs.
        for name in ("history", "dashboard"):
            expected_hash = (
                state["backup_sha256"][name]
            )

            if state["existed"][name]:
                backup = (
                    transaction / f"{name}.old"
                )

                if (
                    not backup.is_file()
                    or not isinstance(
                        expected_hash,
                        str,
                    )
                    or len(expected_hash) != 64
                    or _hash_file(backup)
                    != expected_hash
                ):
                    raise RuntimeError(
                        "Missing or modified KE24 backup; "
                        "manual reconciliation required"
                    )

            elif expected_hash is not None:
                raise RuntimeError(
                    "Invalid KE24 backup metadata"
                )

        # Recovery is safe to repeat if interrupted.
        for name, destination in (
            ("history", history),
            ("dashboard", dashboard),
        ):
            if not state["existed"][name]:
                destination.unlink(
                    missing_ok=True
                )

                _fsync_directory(
                    destination.parent
                )
                continue

            backup = transaction / f"{name}.old"

            if (
                destination.is_file()
                and _hash_file(destination)
                == state["backup_sha256"][name]
            ):
                continue

            descriptor, filename = (
                tempfile.mkstemp(
                    prefix=".ke24_restore_",
                    dir=destination.parent,
                )
            )

            restore = Path(filename)

            try:
                with (
                    os.fdopen(
                        descriptor,
                        "wb",
                    ) as output,
                    backup.open("rb") as source,
                ):
                    shutil.copyfileobj(
                        source,
                        output,
                    )

                    output.flush()
                    os.fsync(output.fileno())

                os.replace(
                    restore,
                    destination,
                )

                _fsync_directory(
                    destination.parent
                )

            finally:
                restore.unlink(
                    missing_ok=True
                )

    shutil.rmtree(transaction)

    _fsync_directory(
        transaction.parent
    )

    return True


@contextmanager
def locked_pretrained_csv_pair(
    history_file,
    dashboard_file,
):
    history, dashboard = _paths(
        history_file,
        dashboard_file,
    )

    lock_files = sorted({
        output.with_name(
            "." + output.name + ".ke24.lock"
        )
        for output in (history, dashboard)
    })

    with ExitStack() as stack:
        for lock in lock_files:
            stack.enter_context(
                _locked_file(lock)
            )

        _recover_locked(
            history,
            dashboard,
        )

        yield


def recover_pretrained_csv_pair(
    history_file,
    dashboard_file,
):
    with locked_pretrained_csv_pair(
        history_file,
        dashboard_file,
    ):
        pass


def _publish_pretrained_csv_pair_locked(
    history,
    dashboard,
    history_file,
    dashboard_file,
):
    history_path, dashboard_path = _paths(
        history_file,
        dashboard_file,
    )

    if (
        not isinstance(history, pd.DataFrame)
        or not isinstance(
            dashboard,
            pd.DataFrame,
        )
    ):
        raise ValueError(
            "Both outputs must be DataFrames"
        )

    transaction = _transaction_dir(
        history_path,
        dashboard_path,
    )

    if transaction.exists():
        raise RuntimeError(
            "Pending KE24 transaction must "
            "be reconciled"
        )

    transaction.mkdir(
        parents=False,
        exist_ok=False,
    )

    _fsync_directory(
        transaction.parent
    )

    state_written = False

    try:
        stage_history = _stage_csv(
            history,
            transaction / "history.new",
        )

        stage_dashboard = _stage_csv(
            dashboard,
            transaction / "dashboard.new",
        )

        existed = {}
        hashes = {}

        for name, destination in (
            ("history", history_path),
            ("dashboard", dashboard_path),
        ):
            existed[name] = destination.exists()
            hashes[name] = None

            if existed[name]:
                backup = (
                    transaction / f"{name}.old"
                )

                _copy_fsynced(
                    destination,
                    backup,
                )

                hashes[name] = _hash_file(
                    backup
                )

        journal = {
            "version": 1,
            "status": "PREPARED",
            "history": str(history_path),
            "dashboard": str(dashboard_path),
            "existed": existed,
            "backup_sha256": hashes,
        }

        _write_state(
            transaction,
            journal,
        )

        state_written = True

        os.replace(
            stage_history,
            history_path,
        )

        _fsync_directory(
            history_path.parent
        )

        os.replace(
            stage_dashboard,
            dashboard_path,
        )

        _fsync_directory(
            dashboard_path.parent
        )

        journal["status"] = "COMMITTED"

        _write_state(
            transaction,
            journal,
        )

        shutil.rmtree(transaction)

        _fsync_directory(
            transaction.parent
        )

    except BaseException:
        if state_written:
            try:
                _recover_locked(
                    history_path,
                    dashboard_path,
                )

            except BaseException as error:
                raise RuntimeError(
                    "KE24 rollback failed; "
                    "stop and reconcile backup "
                    "files manually"
                ) from error
        else:
            shutil.rmtree(
                transaction,
                ignore_errors=True,
            )

        raise


def publish_pretrained_csv_pair(
    history,
    dashboard,
    history_file,
    dashboard_file,
):
    with locked_pretrained_csv_pair(
        history_file,
        dashboard_file,
    ):
        _publish_pretrained_csv_pair_locked(
            history,
            dashboard,
            history_file,
            dashboard_file,
        )
