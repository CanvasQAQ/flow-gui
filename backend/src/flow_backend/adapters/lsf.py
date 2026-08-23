from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import json
import re
import subprocess

from ..batch import BatchManifest
from ..files import atomic_write_json


class LsfSubmissionError(RuntimeError):
    pass


class LsfSubmissionUnknown(LsfSubmissionError):
    """The client lost certainty about whether LSF accepted the submission."""


_JOB_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_JOB_ID = re.compile(r"Job\s+<(\d+)>", re.IGNORECASE)


@dataclass(frozen=True)
class LsfSettings:
    queue: str | None = None
    project: str | None = None
    resource: str | None = None
    application: str | None = None
    array_concurrency: int | None = None
    extra_args: tuple[str, ...] = ()
    submit_timeout_seconds: float = 120.0
    scratch_root: str = "/SCRATCH"
    scratch_user: str | None = None


def build_bsub_command(
    manifest_path: Path,
    manifest: BatchManifest,
    settings: LsfSettings,
    runner_command: Sequence[str] = ("flow-batch-runner",),
) -> list[str]:
    if not _JOB_NAME.fullmatch(manifest.job_name):
        raise LsfSubmissionError("job name may contain only letters, digits, '.', '-', and '_'")
    suffix = ""
    if len(manifest.items) > 1:
        suffix = f"[1-{len(manifest.items)}]"
        if settings.array_concurrency is not None:
            if settings.array_concurrency < 1:
                raise LsfSubmissionError("array concurrency must be positive")
            suffix += f"%{settings.array_concurrency}"
    job_spec = manifest.job_name + suffix
    if len(job_spec) > 255:
        raise LsfSubmissionError("array job specification exceeds the conservative 255-character limit")

    command = ["bsub", "-J", job_spec]
    for flag, value in (
        ("-q", settings.queue),
        ("-P", settings.project),
        ("-R", settings.resource),
        ("-a", settings.application),
    ):
        if value:
            command.extend((flag, value))
    command.extend(settings.extra_args)
    runner = ["env", f"FLOW_SCRATCH_ROOT={settings.scratch_root}"]
    if settings.scratch_user:
        runner.append(f"FLOW_SCRATCH_USER={settings.scratch_user}")
    command.extend((*runner, *runner_command, str(manifest_path)))
    return command


class LsfScheduler:
    def __init__(self, settings: LsfSettings, receipt_directory: Path, dry_run: bool = False):
        self.settings = settings
        self.receipt_directory = receipt_directory
        self.dry_run = dry_run

    def submit(self, manifest_path: Path, job_name: str, settings: dict) -> str:
        manifest = BatchManifest.read(manifest_path)
        if manifest.job_name != job_name:
            raise LsfSubmissionError("manifest job name does not match submission record")
        effective = LsfSettings(**settings) if settings else self.settings
        command = build_bsub_command(manifest_path, manifest, effective)
        if self.dry_run:
            return f"dryrun:{json.dumps(command)}"
        try:
            completed = subprocess.run(
                command, text=True, capture_output=True, check=False,
                timeout=effective.submit_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            receipt = {
                "batchId": manifest.batch_id,
                "command": command,
                "timedOut": True,
                "timeoutSeconds": effective.submit_timeout_seconds,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
            }
            atomic_write_json(self.receipt_directory / f"{manifest.batch_id}.json", receipt)
            raise LsfSubmissionUnknown(
                "bsub timed out; LSF may have accepted the job, so automatic retry is forbidden"
            ) from exc
        receipt = {
            "batchId": manifest.batch_id,
            "command": command,
            "returnCode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        atomic_write_json(self.receipt_directory / f"{manifest.batch_id}.json", receipt)
        if completed.returncode != 0:
            raise LsfSubmissionError(f"bsub failed with exit code {completed.returncode}")
        match = _JOB_ID.search(completed.stdout)
        if not match:
            raise LsfSubmissionUnknown(
                "bsub returned success but its Job ID could not be parsed; automatic retry is forbidden"
            )
        return match.group(1)

    def cancel(self, job_id: str) -> None:
        completed = subprocess.run(["bkill", job_id], text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise LsfSubmissionError(f"bkill failed: {completed.stderr.strip()}")
