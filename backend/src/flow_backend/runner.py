from datetime import datetime, timezone
from pathlib import Path
import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess

from .batch import BatchManifest, attempt_return_path, write_execution_status


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,252}[A-Za-z0-9])?")
_IDENTITY_FILE = ".flowpilot-attempt.json"


def _expand_command(command: tuple[str, ...], item, sp_path: Path, work_path: Path) -> list[str]:
    replacements = {
        "{sp_path}": str(sp_path),
        "{case_path}": str(work_path),
        "{testcase_id}": item.testcase_id,
        "{attempt_id}": item.attempt_id,
    }
    return [replacements.get(token, token) for token in command]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(path: Path, state: str, base: dict, **fields) -> None:
    write_execution_status(path, state=state, **fields, **base)


def _scratch_path(root: Path, user: str, attempt_id: str) -> Path:
    if not _SAFE_ID.fullmatch(user) or not _SAFE_ID.fullmatch(attempt_id):
        raise ValueError("unsafe Scratch user or Attempt ID")
    root = root.resolve()
    result = root / user / "flowpilot" / attempt_id
    # Components are validated above; this guards future path construction changes.
    if root not in result.parents:
        raise ValueError("Scratch path escapes configured root")
    current = root
    for component in (user, "flowpilot", attempt_id):
        current = current / component
        if current.exists() and current.is_symlink():
            raise ValueError(f"Scratch path component is a symbolic link: {current}")
    return result


def _create_hostname_marker(return_path: Path, hostname: str) -> str | None:
    try:
        if not _HOSTNAME.fullmatch(hostname):
            raise ValueError(f"unsafe hostname: {hostname!r}")
        return_path.mkdir(parents=True, exist_ok=True)
        marker = return_path / hostname
        if marker.exists():
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_size != 0:
                raise ValueError(f"hostname marker path is not an empty regular file: {marker}")
        else:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        return None
    except Exception as exc:  # Marker creation is deliberately best-effort.
        return f"{type(exc).__name__}: {exc}"


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"stage-out refuses symbolic link: {path}")


def _copy_all(source: Path, destination: Path) -> None:
    _reject_symlinks(source)
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
    for source_file in (path for path in source.rglob("*") if path.is_file()):
        copied = destination / source_file.relative_to(source)
        if not copied.is_file() or copied.stat().st_size != source_file.stat().st_size:
            raise OSError(f"stage-out verification failed: {copied}")


def run_item(
    manifest_path: Path,
    index: int,
    *,
    scratch_root: Path | None = None,
    scratch_user: str | None = None,
) -> int:
    manifest = BatchManifest.read(manifest_path)
    item = manifest.item(index)
    case_path = Path(item.case_path)
    status_path = Path(item.status_path)
    return_path = attempt_return_path(item)
    root = scratch_root or Path(os.environ.get("FLOW_SCRATCH_ROOT", "/SCRATCH"))
    user = scratch_user or os.environ.get("FLOW_SCRATCH_USER") or getpass.getuser()
    started_at = _utcnow()
    base = {
        "batchId": manifest.batch_id,
        "testcaseId": item.testcase_id,
        "attemptId": item.attempt_id,
        "arrayIndex": item.index,
        "startedAt": started_at,
    }
    hostname = socket.gethostname()
    marker_error = _create_hostname_marker(return_path, hostname)
    scratch_path: Path | None = None
    simulator_exit: int | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    try:
        _write_status(
            status_path, "staging_in", base, hostname=hostname,
            hostnameMarkerError=marker_error, returnPath=str(return_path),
        )
        source_sp = Path(item.sp_path)
        if not source_sp.is_file() or source_sp.is_symlink():
            raise FileNotFoundError(f"SP input is not a regular file: {source_sp}")
        scratch_path = _scratch_path(root, user, item.attempt_id)
        scratch_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if scratch_path.is_symlink():
            raise ValueError(f"Scratch Attempt path is a symbolic link: {scratch_path}")
        scratch_sp = scratch_path / source_sp.name
        shutil.copy2(source_sp, scratch_sp)
        if scratch_sp.stat().st_size != source_sp.stat().st_size:
            raise OSError("SP stage-in size verification failed")
        (scratch_path / _IDENTITY_FILE).write_text(
            json.dumps({"attemptId": item.attempt_id, "batchId": manifest.batch_id}),
            encoding="utf-8",
        )
        _write_status(
            status_path, "running", base, hostname=hostname, scratchPath=str(scratch_path),
            returnPath=str(return_path), hostnameMarkerError=marker_error,
        )
        command = _expand_command(manifest.command, item, scratch_sp, scratch_path)
        stdout_path = scratch_path / f"simulator.{item.attempt_id}.stdout.log"
        stderr_path = scratch_path / f"simulator.{item.attempt_id}.stderr.log"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(
                command, cwd=scratch_path, stdout=stdout, stderr=stderr, check=False
            )
        simulator_exit = completed.returncode
        missing = [name for name in manifest.expected_files if not (scratch_path / name).is_file()]
        simulation_succeeded = simulator_exit == 0 and not missing
        _write_status(
            status_path, "staging_out", base, hostname=hostname,
            scratchPath=str(scratch_path), returnPath=str(return_path),
            simulatorExitCode=simulator_exit, missingExpectedFiles=missing,
            hostnameMarkerError=marker_error,
        )
        try:
            _copy_all(scratch_path, return_path)
        except Exception as exc:
            state = "copyback_waiting" if simulation_succeeded else "simulation_failed"
            _write_status(
                status_path, state, base, hostname=hostname, scratchPath=str(scratch_path),
                returnPath=str(return_path), simulatorExitCode=simulator_exit,
                wrapperExitCode=30 if simulation_succeeded else 20,
                missingExpectedFiles=missing, hostnameMarkerError=marker_error,
                copybackError={"type": type(exc).__name__, "message": str(exc)},
                finishedAt=_utcnow(),
            )
            return 30 if simulation_succeeded else 20
        state = "complete" if simulation_succeeded else "simulation_failed"
        _write_status(
            status_path, state, base, hostname=hostname, scratchPath=str(scratch_path),
            returnPath=str(return_path), simulatorExitCode=simulator_exit,
            wrapperExitCode=0 if simulation_succeeded else 20,
            missingExpectedFiles=missing, hostnameMarkerError=marker_error,
            stdoutPath=str(return_path / stdout_path.name),
            stderrPath=str(return_path / stderr_path.name), finishedAt=_utcnow(),
        )
        return 0 if simulation_succeeded else 20
    except Exception as exc:
        _write_status(
            status_path, "simulation_failed", base, hostname=hostname,
            scratchPath=str(scratch_path) if scratch_path else None,
            returnPath=str(return_path), simulatorExitCode=simulator_exit,
            wrapperExitCode=21, hostnameMarkerError=marker_error,
            error={"type": type(exc).__name__, "message": str(exc)}, finishedAt=_utcnow(),
        )
        return 21


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one immutable FlowPilot Batch item")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    index = args.index or int(os.environ.get("LSB_JOBINDEX", "1"))
    raise SystemExit(run_item(args.manifest, index))


if __name__ == "__main__":
    main()
