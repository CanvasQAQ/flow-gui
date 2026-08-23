from copy import deepcopy
from pathlib import Path
from typing import Any
import json
import shutil


DEFAULT_SITE_CONFIG: dict[str, Any] = {
    "hspice": {
        "initialFields": {
            "rangesel": "rangeselcode",
            "vrefsel_0": "vrefsel0code",
            "vrefsel_90": "vrefsel90code",
            "vrefsel_180": "vrefsel180code",
            "vrefsel_270": "vrefsel270code",
            "legsel_0": "legsel0code",
            "legsel_90": "legsel90code",
            "legsel_180": "legsel180code",
            "legsel_270": "legsel270code"
        },
        "resultFields": {
            "loss_0": "loss_0",
            "loss_90": "loss_90",
            "loss_180": "loss_180",
            "loss_270": "loss_270"
        },
        "pvtParameterNames": {"vdd": "vdd", "vcm": "vcm"},
        "simulatorCommand": ["hspice", "-i", "{sp_path}", "-o", "simulation"],
        "expectedFiles": ["simulation.lis"]
    },
    "dataset": {
        "spPattern": "^(?P<stem>.+)\\.sp(?P<corner>[^/]*)$",
        "mtTemplate": "{stem}.mt{corner}",
        "recursive": False
    },
    "lsf": {
        "queue": None,
        "project": None,
        "resource": None,
        "application": None,
        "arrayConcurrency": None,
        "extraArgs": [],
        "submitTimeoutSeconds": 120,
        "dryRun": True
    },
    "snapshot": {"path": None, "columns": {}},
    "scratch": {
        "root": "/SCRATCH",
        "user": None,
        "sshProgram": "ssh",
        "sshTimeoutSeconds": 300,
        "realMachineVerificationRequired": True
    },
    "postprocess": {"command": [], "mtFilePattern": "*.mt*", "maxConcurrency": 1},
    "algorithms": {}
}


_REPLACE_OBJECT_KEYS = {"initialFields", "resultFields", "columns", "algorithms"}


def _merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if key not in _REPLACE_OBJECT_KEYS and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_site_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return deepcopy(DEFAULT_SITE_CONFIG)
    override = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(override, dict):
        raise ValueError("site config root must be a JSON object")
    return _merge(DEFAULT_SITE_CONFIG, override)


def validate_site_config(config: dict[str, Any]) -> list[dict[str, str]]:
    checks = []
    simulator = config["hspice"].get("simulatorCommand", [])
    simulator_program = simulator[0] if simulator else None
    checks.append({
        "name": "simulatorCommand",
        "status": "ok" if simulator_program and shutil.which(simulator_program) else "pending",
        "detail": simulator_program or "not configured",
    })
    checks.append({
        "name": "bsub",
        "status": "ok" if shutil.which("bsub") else "pending",
        "detail": shutil.which("bsub") or "not found on this machine",
    })
    snapshot_path = config.get("snapshot", {}).get("path")
    checks.append({
        "name": "schedulerSnapshot",
        "status": "ok" if snapshot_path and Path(snapshot_path).is_file() else "pending",
        "detail": snapshot_path or "not configured",
    })
    postprocess = config.get("postprocess", {}).get("command", [])
    checks.append({
        "name": "postprocessCommand",
        "status": "ok" if postprocess and shutil.which(postprocess[0]) else "pending",
        "detail": postprocess[0] if postprocess else "not configured",
    })
    algorithms = config.get("algorithms", {})
    checks.append({
        "name": "algorithms",
        "status": "ok" if algorithms else "pending",
        "detail": ", ".join(sorted(algorithms)) if algorithms else "no algorithm scheme configured",
    })
    scratch = config.get("scratch", {})
    checks.append({
        "name": "scratchRoot",
        "status": "ok" if scratch.get("root") == "/SCRATCH" else "pending",
        "detail": f"{scratch.get('root') or 'not configured'}; verify on an LSF execution host",
    })
    ssh_program = scratch.get("sshProgram", "ssh")
    checks.append({
        "name": "copybackSsh",
        "status": "ok" if shutil.which(ssh_program) else "pending",
        "detail": f"{ssh_program}; known_hosts/authentication require real-machine verification",
    })
    return checks
