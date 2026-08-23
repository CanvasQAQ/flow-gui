"""Deterministic, in-memory Backend used for frontend development."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__


CORNER_STATES = (
    "done", "running", "queued", "postprocess", "preparing", "not_started",
    "failed", "status_unknown", "waiting_user", "copyback_waiting",
)


class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1)
    convention: dict | None = None


class ScanDatasetRequest(BaseModel):
    mode: Literal["append", "overwrite"] = "append"


class CreateRunRequest(BaseModel):
    datasetId: str
    name: str = Field(min_length=1, max_length=200)
    pvt: dict[str, object]
    algorithmScheme: str
    algorithmConfig: dict[str, object] = Field(default_factory=dict)
    cornerNumbers: list[int] = Field(min_length=1)


class AddCornersRequest(BaseModel):
    cornerNumbers: list[int] = Field(min_length=1)


class PauseRequest(BaseModel):
    paused: bool


class RecoveryFilterRequest(BaseModel):
    testcaseIds: list[str] = Field(default_factory=list)
    cornerNumbers: list[int] = Field(default_factory=list)
    stageKeys: list[str] = Field(default_factory=list)
    algorithmCallNumbers: list[int] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)


class RecoveryPreviewRequest(BaseModel):
    action: Literal["resubmit", "ignore"]
    filters: RecoveryFilterRequest


class RecoveryConfirmRequest(BaseModel):
    token: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seeded(index: int, factor: int = 37) -> float:
    return ((index * factor + 17) % 997) / 997


def _pick_state(index: int, scenario: str) -> str:
    n = index % 100
    if scenario == "failures":
        if n < 24:
            return "failed"
        if n < 44:
            return "status_unknown"
        if n < 58:
            return "waiting_user"
        if n < 66:
            return "copyback_waiting"
        if n < 82:
            return "running"
        return "done"
    if n < 58:
        return "done"
    if n < 67:
        return "running"
    if n < 73:
        return "queued"
    if n < 77:
        return "postprocess"
    if n < 82:
        return "preparing"
    if n < 87:
        return "not_started"
    if n < 92:
        return "failed"
    if n < 96:
        return "status_unknown"
    if n < 99:
        return "waiting_user"
    return "copyback_waiting"


def _corner(number: int, scenario: str, state: str | None = None) -> dict:
    index = number - 1
    resolved_state = state or _pick_state(index, scenario)
    if resolved_state in {"not_started", "preparing"}:
        stage = "stage1"
    elif resolved_state == "done":
        stage = "stage3"
    else:
        stage = ("stage1", "stage2", "stage3")[index % 3]
    total = 0 if resolved_state == "not_started" else 5 + (index % 7) * 2
    complete = total if resolved_state == "done" else min(total, int(_seeded(index) * total))
    issue = None
    if resolved_state == "failed":
        issue = "LSF ended, but no matching status_complete was found for this execution."
    elif resolved_state == "status_unknown":
        issue = "The Job is absent from the latest public snapshot."
    elif resolved_state == "waiting_user":
        issue = "The current algorithm-call group contains unresolved failed Testcases."
    elif resolved_state == "copyback_waiting":
        issue = "Simulation finished, but results could not be copied back."
    steps = []
    step_keys = ("algorithm_decision", "prepare_cases", "submit_batch", "simulate_group", "recovery_gate", "postprocess")
    completed_steps = 6 if resolved_state == "done" else max(0, (index % 5))
    for step_index, key in enumerate(step_keys):
        step_state = "complete" if step_index < completed_steps else "waiting"
        if step_index == completed_steps and resolved_state not in {"not_started", "done"}:
            step_state = "attention" if issue else "ongoing"
        steps.append({"key": key, "state": step_state})
    return {
        "id": f"corner_{number:04d}", "number": number, "state": resolved_state,
        "status": resolved_state, "stage": stage, "round": index % 3 + 1,
        "testcaseTotal": total, "testcaseDone": complete,
        "inputVersion": "v2" if index % 23 == 0 else "v1",
        "initialCode": {
            "rangesel": (index * 3) % 32,
            "vrefsel": [(index + phase * 2) % 8 for phase in range(4)],
            "legsel": [(index + phase) % 5 for phase in range(4)],
        },
        "updateAvailable": index % 79 == 0,
        "loss": round(0.18 + _seeded(index, 71) * 0.35, 4) if resolved_state == "done" else None,
        "updatedAt": f"2026-08-22T{16 - index % 7:02d}:{index * 7 % 60:02d}:00+08:00",
        "issue": issue, "workflowSteps": steps,
    }


def _recovery_cases(corners: list[dict]) -> list[dict]:
    items = []
    review_states = {"failed", "status_unknown", "waiting_user", "copyback_waiting"}
    for corner in (item for item in corners if item["state"] in review_states):
        for case_index in range(2 if corner["number"] % 3 == 1 else 1):
            testcase_id = f"tc_{corner['number']:04d}_{case_index + 1}"
            items.append({
                "testcaseId": testcase_id, "name": f"tc_{case_index + 1 + corner['number'] % 9:03d}",
                "status": corner["state"], "cornerNumber": corner["number"],
                "stageKey": corner["stage"], "callNumber": corner["round"],
                "attemptId": f"attempt_{corner['number']:04d}_{case_index + 1}",
                "attempt": case_index + 1,
                "lsfJobId": None if corner["state"] == "status_unknown" else str(812400 + corner["number"]),
                "evidence": {"message": corner["issue"]},
                "copybackError": {"message": corner["issue"]} if corner["state"] == "copyback_waiting" else None,
                "updatedAt": corner["updatedAt"],
            })
    return items


def _run(run_id: str, name: str, count: int, scenario: str, status: str = "running") -> dict:
    corners = [_corner(number, scenario) for number in range(1, count + 1)]
    return {
        "id": run_id, "name": name, "datasetId": "dataset-01",
        "datasetName": "adc_pvt_baseline_2026Q3", "status": status,
        "pvt": {"vdd": "0.72 V", "vcm": "0.36 V"},
        "algorithm": "reference-search", "createdAt": "2026-08-22T09:18:00+08:00",
        "corners": corners, "recoveryCases": _recovery_cases(corners),
    }


def _initial_snapshot(scenario: str) -> dict:
    if scenario == "empty":
        return {"revision": 1, "datasets": [], "runs": []}
    count = 5000 if scenario == "large" else 160 if scenario == "failures" else 1248
    first = _run("run-20260822-01", "VT drift · nominal batch", count, scenario)
    second = _run("run-20260821-02", "VT drift · low VDD comparison", 384, scenario, "paused")
    return {
        "revision": 1,
        "datasets": [{
            "id": "dataset-01", "name": "adc_pvt_baseline_2026Q3",
            "path": "/demo/adc/calibration/baseline", "scannedAt": "2026-08-22T15:42:00+08:00",
            "total": max(count, 1312), "available": max(count, 1284),
            "invalid": 21, "missing": 7, "version": 4,
        }],
        "runs": [first, second],
    }


class DemoStore:
    def __init__(self, scenario: str):
        self.scenario = scenario
        self.snapshot = _initial_snapshot(scenario)
        self.lock = threading.RLock()
        self.previews: dict[str, dict] = {}

    def read(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.snapshot)

    def changed(self) -> int:
        self.snapshot["revision"] += 1
        return self.snapshot["revision"]

    def find_run(self, run_id: str) -> dict:
        run = next((item for item in self.snapshot["runs"] if item["id"] == run_id), None)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return run


def create_demo_app(scenario: str | None = None) -> FastAPI:
    selected_scenario = scenario or os.environ.get("FLOWPILOT_DEMO_SCENARIO", "default")
    if selected_scenario not in {"default", "failures", "large", "empty"}:
        raise ValueError(f"Unknown demo scenario: {selected_scenario}")
    store = DemoStore(selected_scenario)
    application = FastAPI(title="FlowPilot Demo Backend", version=__version__)
    application.state.shutdown_requested = threading.Event()
    application.state.demo_store = store
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "null"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )

    def runtime() -> dict:
        return {
            "backend": {
                "state": "ready", "message": f"Demo Backend · {selected_scenario}",
                "configured": True, "mode": "demo", "scenario": selected_scenario,
            },
            "checker": {"state": "idle", "message": "Synthetic status stream ready"},
            "workers": {},
        }

    @application.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mode": "demo", "scenario": selected_scenario}

    @application.get("/api/v1/runtime/status")
    def runtime_status() -> dict:
        return runtime()

    @application.get("/api/v1/ui/snapshot")
    def ui_snapshot() -> dict:
        result = store.read()
        result["runtime"] = runtime()
        return result

    @application.get("/api/v1/datasets")
    def list_datasets() -> dict:
        return {"items": store.read()["datasets"]}

    @application.get("/api/v1/runs")
    def list_runs() -> dict:
        snapshot = store.read()
        return {"items": [{key: value for key, value in run.items() if key not in {"corners", "recoveryCases"}} for run in snapshot["runs"]]}

    @application.get("/api/v1/runs/{run_id}/corners")
    def list_run_corners(run_id: str) -> dict:
        with store.lock:
            return {"items": copy.deepcopy(store.find_run(run_id)["corners"])}

    @application.get("/api/v1/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream():
            cursor = store.read()["revision"]
            while not application.state.shutdown_requested.is_set() and not await request.is_disconnected():
                revision = store.read()["revision"]
                if revision > cursor:
                    cursor = revision
                    payload = {"id": cursor, "type": "demo.changed", "createdAt": _now()}
                    yield f"id: {cursor}\nevent: state\ndata: {json.dumps(payload)}\n\n"
                else:
                    yield f": demo heartbeat {cursor}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @application.post("/api/v1/datasets")
    def create_dataset(request: CreateDatasetRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        dataset_id = f"demo-dataset-{sha256(idempotency_key.encode()).hexdigest()[:8]}"
        with store.lock:
            store.snapshot["datasets"].insert(0, {
                "id": dataset_id, "name": request.name, "path": request.path,
                "scannedAt": "Not scanned", "total": 0, "available": 0,
                "invalid": 0, "missing": 0, "version": 1,
            })
            store.changed()
        return {"datasetId": dataset_id}

    @application.post("/api/v1/datasets/{dataset_id}/scan")
    def scan_dataset(dataset_id: str, _: ScanDatasetRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            dataset = next((item for item in store.snapshot["datasets"] if item["id"] == dataset_id), None)
            if dataset is None:
                raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
            dataset.update({"scannedAt": _now(), "total": 128, "available": 124, "invalid": 3, "missing": 1})
            store.changed()
        return {"datasetId": dataset_id, "status": "complete", "total": 128}

    @application.post("/api/v1/runs")
    def create_run(request: CreateRunRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            dataset = next((item for item in store.snapshot["datasets"] if item["id"] == request.datasetId), None)
            if dataset is None:
                raise HTTPException(status_code=422, detail=f"Dataset not found: {request.datasetId}")
            run_id = f"demo-run-{sha256(idempotency_key.encode()).hexdigest()[:8]}"
            corners = [_corner(number, selected_scenario, "preparing") for number in sorted(set(request.cornerNumbers))]
            store.snapshot["runs"].insert(0, {
                "id": run_id, "name": request.name, "datasetId": dataset["id"],
                "datasetName": dataset["name"], "status": "running", "pvt": request.pvt,
                "algorithm": request.algorithmScheme, "createdAt": _now(), "corners": corners,
                "recoveryCases": [],
            })
            store.changed()
        return {"runId": run_id, "cornerCount": len(corners), "replayed": False}

    @application.post("/api/v1/runs/{run_id}/pause")
    def pause_run(run_id: str, request: PauseRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            run = store.find_run(run_id)
            run["status"] = "paused" if request.paused else "running"
            store.changed()
        return {"runId": run_id, "status": run["status"], "replayed": False}

    @application.post("/api/v1/runs/{run_id}/corners")
    def add_corners(run_id: str, request: AddCornersRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            run = store.find_run(run_id)
            existing = {item["number"] for item in run["corners"]}
            additions = [_corner(number, selected_scenario, "not_started") for number in sorted(set(request.cornerNumbers) - existing)]
            run["corners"].extend(additions)
            run["corners"].sort(key=lambda item: item["number"])
            store.changed()
        return {"runId": run_id, "added": len(additions), "replayed": False}

    @application.post("/api/v1/runs/{run_id}/case-recovery/preview")
    def preview_recovery(run_id: str, request: RecoveryPreviewRequest) -> dict:
        with store.lock:
            run = store.find_run(run_id)
            available = {item["testcaseId"] for item in run["recoveryCases"]}
            selected = [item for item in request.filters.testcaseIds if item in available]
            if not selected:
                raise HTTPException(status_code=422, detail="No matching Demo Testcases")
            token = f"demo-preview-{len(store.previews) + 1}"
            store.previews[token] = {"runId": run_id, "action": request.action, "testcaseIds": selected}
        return {"token": token, "action": request.action, "matched": len(selected), "expiresAt": "never"}

    @application.post("/api/v1/case-recovery/confirm")
    def confirm_recovery(request: RecoveryConfirmRequest, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            preview = store.previews.pop(request.token, None)
            if preview is None:
                raise HTTPException(status_code=422, detail="Demo preview token is invalid")
            run = store.find_run(preview["runId"])
            selected = set(preview["testcaseIds"])
            affected_numbers = {item["cornerNumber"] for item in run["recoveryCases"] if item["testcaseId"] in selected}
            run["recoveryCases"] = [item for item in run["recoveryCases"] if item["testcaseId"] not in selected]
            target_state = "queued" if preview["action"] == "resubmit" else "ignored"
            for corner in run["corners"]:
                if corner["number"] in affected_numbers:
                    corner.update(_corner(corner["number"], selected_scenario, target_state))
            store.changed()
        return {"action": preview["action"], "affected": len(selected), "replayed": False}

    @application.post("/api/v1/execution-attempts/{attempt_id}/copyback", status_code=202)
    def retry_copyback(attempt_id: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
        with store.lock:
            found = False
            for run in store.snapshot["runs"]:
                matched = next((
                    item for item in run["recoveryCases"]
                    if item["attemptId"] == attempt_id and item["status"] == "copyback_waiting"
                ), None)
                if matched is not None:
                    run["recoveryCases"].remove(matched)
                    corner = next((item for item in run["corners"] if item["number"] == matched["cornerNumber"]), None)
                    if corner is not None:
                        corner.update(_corner(corner["number"], selected_scenario, "postprocess"))
                    found = True
            if not found:
                raise HTTPException(status_code=422, detail=f"Copy-back Attempt not found: {attempt_id}")
            store.changed()
        return {"attemptId": attempt_id, "status": "queued", "workId": f"demo-{store.snapshot['revision']}"}

    return application


app = create_demo_app()
