from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import json
import re
import shlex
import subprocess


CopyBackErrorCode = Literal[
    "hostname_marker_missing",
    "hostname_marker_invalid",
    "ssh_connection_failed",
    "remote_source_missing",
    "destination_unwritable",
    "copy_command_failed",
    "copy_verification_failed",
]

_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,252}[A-Za-z0-9])?")
_IDENTITY_FILE = ".flowpilot-attempt.json"


class CopyBackError(RuntimeError):
    def __init__(self, code: CopyBackErrorCode, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CopyBackRequest:
    attempt_id: str
    scratch_path: Path
    return_path: Path


@dataclass(frozen=True)
class CopyBackResult:
    hostname: str
    return_path: str


def find_hostname_marker(return_path: Path) -> str:
    if not return_path.is_dir():
        raise CopyBackError("hostname_marker_missing", "Attempt return directory does not exist")
    candidates = []
    for path in return_path.iterdir():
        if path.is_file() and not path.is_symlink() and path.stat().st_size == 0:
            if _HOSTNAME.fullmatch(path.name):
                candidates.append(path.name)
    if not candidates:
        raise CopyBackError("hostname_marker_missing", "No hostname marker exists for this Attempt")
    if len(candidates) != 1:
        raise CopyBackError(
            "hostname_marker_invalid",
            "Attempt has more than one hostname marker",
            ", ".join(sorted(candidates)),
        )
    return candidates[0]


class SshCopyBackAdapter:
    """Retry stage-out through ordinary OpenSSH without weakening host-key checks."""

    def __init__(self, ssh_program: str = "ssh", timeout_seconds: int = 300):
        self.ssh_program = ssh_program
        self.timeout_seconds = timeout_seconds

    def copy(self, request: CopyBackRequest) -> CopyBackResult:
        hostname = find_hostname_marker(request.return_path)
        source = str(request.scratch_path)
        destination = str(request.return_path)
        remote = shlex.join([
            "sh", "-c",
            # The paths are passed as positional parameters, not interpolated as shell code.
            'test -d "$1" || exit 41; test -d "$2" || exit 42; '
            'test -w "$2" || exit 43; cp -a "$1"/. "$2"/ || exit 44',
            "flow-copyback", source, destination,
        ])
        try:
            completed = subprocess.run(
                [self.ssh_program, hostname, remote],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise CopyBackError("ssh_connection_failed", "SSH copy-back could not run", str(exc)) from exc
        detail = (completed.stderr or completed.stdout or "").strip()
        errors: dict[int, tuple[CopyBackErrorCode, str]] = {
            41: ("remote_source_missing", "Scratch source directory does not exist"),
            42: ("destination_unwritable", "Attempt return directory does not exist"),
            43: ("destination_unwritable", "Attempt return directory is not writable"),
            44: ("copy_command_failed", "Remote copy command failed"),
            255: ("ssh_connection_failed", "SSH connection failed"),
        }
        if completed.returncode:
            code, message = errors.get(
                completed.returncode, ("copy_command_failed", "Remote copy command failed")
            )
            raise CopyBackError(code, message, detail)
        identity_path = request.return_path / _IDENTITY_FILE
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CopyBackError(
                "copy_verification_failed", "Copied Attempt identity cannot be read", str(exc)
            ) from exc
        if identity.get("attemptId") != request.attempt_id:
            raise CopyBackError(
                "copy_verification_failed", "Copied result belongs to a different Attempt"
            )
        return CopyBackResult(hostname=hostname, return_path=destination)
