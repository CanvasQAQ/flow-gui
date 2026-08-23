from pathlib import Path
import subprocess
import sys

import pytest

from flow_backend.adapters.lsf import (
    LsfScheduler, LsfSettings, LsfSubmissionUnknown, build_bsub_command,
)
from flow_backend.batch import (
    BatchItem,
    BatchManifest,
    ManifestError,
    read_execution_status,
)
from flow_backend.runner import run_item


def make_manifest(tmp_path: Path, count: int = 2) -> tuple[BatchManifest, Path]:
    items = []
    for index in range(1, count + 1):
        case = (tmp_path / f"case_{index}").resolve()
        case.mkdir()
        sp = case / "testcase.sp"
        sp.write_text("test", encoding="utf-8")
        items.append(BatchItem(index, f"tc{index}", f"attempt{index}", str(case), str(sp), str(case / "status.json")))
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('wave.raw').write_text('ok')",
    ]
    manifest = BatchManifest.create("batch1", "flow_batch1", command, items, ["wave.raw"])
    path = tmp_path / "batch.json"
    manifest.write_once(path)
    return manifest, path


def test_manifest_is_immutable_and_runner_status_matches_attempt(tmp_path: Path) -> None:
    manifest, path = make_manifest(tmp_path)
    with pytest.raises(ManifestError, match="immutable"):
        manifest.write_once(path)
    assert run_item(path, 2, scratch_root=tmp_path / "scratch", scratch_user="tester") == 0
    status = read_execution_status(Path(manifest.items[1].status_path), "attempt2")
    assert status["state"] == "complete"
    assert status["simulatorExitCode"] == 0
    return_path = Path(manifest.items[1].case_path) / ".flowpilot" / "attempts" / "attempt2"
    assert (return_path / "testcase.sp").read_text(encoding="utf-8") == "test"
    assert (return_path / "wave.raw").read_text(encoding="utf-8") == "ok"
    assert status["scratchPath"].startswith(str(tmp_path / "scratch"))
    with pytest.raises(ManifestError, match="expected 'other'"):
        read_execution_status(Path(manifest.items[1].status_path), "other")


def test_build_array_command_without_shell_interpolation(tmp_path: Path) -> None:
    manifest, path = make_manifest(tmp_path)
    command = build_bsub_command(
        path,
        manifest,
        LsfSettings(queue="normal", project="chip", resource="rusage[mem=1024]", array_concurrency=4),
    )
    assert command[:3] == ["bsub", "-J", "flow_batch1[1-2]%4"]
    assert command[-2:] == ["flow-batch-runner", str(path)]


def test_bsub_timeout_is_ambiguous_and_writes_receipt(tmp_path: Path, monkeypatch) -> None:
    manifest, path = make_manifest(tmp_path, count=1)
    scheduler = LsfScheduler(
        LsfSettings(submit_timeout_seconds=45), tmp_path / "receipts"
    )

    def time_out(*args, **kwargs):
        assert kwargs["timeout"] == 45
        raise subprocess.TimeoutExpired(args[0], 45, output="maybe accepted")

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(LsfSubmissionUnknown, match="automatic retry is forbidden"):
        scheduler.submit(path, manifest.job_name, {})
    assert (tmp_path / "receipts" / "batch1.json").is_file()


def test_success_without_parseable_job_id_is_ambiguous(tmp_path: Path, monkeypatch) -> None:
    manifest, path = make_manifest(tmp_path, count=1)
    scheduler = LsfScheduler(LsfSettings(), tmp_path / "receipts")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "submitted", ""),
    )

    with pytest.raises(LsfSubmissionUnknown, match="automatic retry is forbidden"):
        scheduler.submit(path, manifest.job_name, {})


def test_runner_hostname_marker_is_best_effort(tmp_path: Path, monkeypatch) -> None:
    manifest, path = make_manifest(tmp_path, count=1)
    monkeypatch.setattr("flow_backend.runner.socket.gethostname", lambda: "bad/hostname")

    assert run_item(path, 1, scratch_root=tmp_path / "scratch", scratch_user="tester") == 0
    status = read_execution_status(Path(manifest.items[0].status_path), "attempt1")
    assert status["state"] == "complete"
    assert "unsafe hostname" in status["hostnameMarkerError"]


def test_runner_copy_failure_waits_for_copyback(tmp_path: Path, monkeypatch) -> None:
    manifest, path = make_manifest(tmp_path, count=1)
    monkeypatch.setattr("flow_backend.runner.socket.gethostname", lambda: "compute-01")
    monkeypatch.setattr(
        "flow_backend.runner._copy_all",
        lambda *args: (_ for _ in ()).throw(OSError("network disk full")),
    )

    assert run_item(path, 1, scratch_root=tmp_path / "scratch", scratch_user="tester") == 30
    status = read_execution_status(Path(manifest.items[0].status_path), "attempt1")
    assert status["state"] == "copyback_waiting"
    assert status["copybackError"]["message"] == "network disk full"
    marker = Path(status["returnPath"]) / "compute-01"
    assert marker.is_file() and marker.stat().st_size == 0
