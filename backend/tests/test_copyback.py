from pathlib import Path
import json
import subprocess

import pytest

from flow_backend.adapters.copyback import (
    CopyBackError, CopyBackRequest, SshCopyBackAdapter, find_hostname_marker,
)


def make_request(tmp_path: Path) -> CopyBackRequest:
    destination = tmp_path / "case" / ".flowpilot" / "attempts" / "attempt1"
    destination.mkdir(parents=True)
    (destination / "compute-01").touch()
    return CopyBackRequest("attempt1", Path("/SCRATCH/tester/flowpilot/attempt1"), destination)


def test_copyback_uses_marker_and_ordinary_ssh(tmp_path: Path, monkeypatch) -> None:
    request = make_request(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        (request.return_path / ".flowpilot-attempt.json").write_text(
            json.dumps({"attemptId": "attempt1"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = SshCopyBackAdapter(timeout_seconds=17).copy(request)
    assert result.hostname == "compute-01"
    assert calls[0][0][0:2] == ["ssh", "compute-01"]
    assert "StrictHostKeyChecking" not in " ".join(calls[0][0])
    assert calls[0][1]["timeout"] == 17


@pytest.mark.parametrize(
    ("returncode", "code"),
    [(41, "remote_source_missing"), (43, "destination_unwritable"),
     (44, "copy_command_failed"), (255, "ssh_connection_failed")],
)
def test_copyback_categorizes_remote_errors(
    tmp_path: Path, monkeypatch, returncode: int, code: str
) -> None:
    request = make_request(tmp_path)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, "", "detail"),
    )
    with pytest.raises(CopyBackError) as caught:
        SshCopyBackAdapter().copy(request)
    assert caught.value.code == code
    assert caught.value.detail == "detail"


def test_copyback_rejects_ambiguous_hostname_markers(tmp_path: Path) -> None:
    request = make_request(tmp_path)
    (request.return_path / "compute-02").touch()
    with pytest.raises(CopyBackError) as caught:
        find_hostname_marker(request.return_path)
    assert caught.value.code == "hostname_marker_invalid"
