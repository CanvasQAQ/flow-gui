from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Literal
import asyncio
import json
import logging
import threading

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .adapters.hspice import HspiceMeasureParser
from .adapters.hspice import HspiceSimulator
from .adapters.algorithm import AlgorithmLoadError, AlgorithmRegistry, PythonAlgorithmAdapter
from .adapters.lsf import LsfScheduler, LsfSettings
from .adapters.postprocess import HspiceMtPostprocess
from .adapters.copyback import SshCopyBackAdapter
from .adapters.snapshot import ParquetSchedulerSnapshot, SnapshotColumns
from .config import Settings
from .coordinator import WorkflowCoordinator
from .db import SCHEMA_VERSION, migrate
from .repository import QueryRepository
from .runtime import RuntimeWorkerSupervisor, WorkerLaneConfig
from .services.datasets import DatasetConvention, DatasetScanError, DatasetService
from .services.runs import RunService, RunServiceError
from .services.recovery import CaseFilter, CaseRecoveryService, RecoveryError
from .services.status_monitor import (
    SqliteRuntimeHealthSink, SqliteSnapshotIdentityStore, SqliteStatusMonitorStore,
    StatusMonitor, StatusMonitorWorker,
)
from .site_config import load_site_config, validate_site_config


class DatasetConventionRequest(BaseModel):
    spPattern: str | None = None
    mtTemplate: str | None = None
    recursive: bool | None = None


class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1)
    convention: DatasetConventionRequest | None = None


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

    def to_domain(self) -> CaseFilter:
        return CaseFilter(
            testcase_ids=tuple(self.testcaseIds),
            corner_numbers=tuple(self.cornerNumbers),
            stage_keys=tuple(self.stageKeys),
            algorithm_call_numbers=tuple(self.algorithmCallNumbers),
            statuses=tuple(self.statuses),
        )


class RecoveryPreviewRequest(BaseModel):
    action: Literal["resubmit", "ignore"]
    filters: RecoveryFilterRequest


class RecoveryConfirmRequest(BaseModel):
    token: str


def _stable_id(prefix: str, key: str, scope: str) -> str:
    return f"{prefix}_{sha256(f'{scope}:{key}'.encode()).hexdigest()[:24]}"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    site_config = load_site_config(resolved_settings.site_config_path)
    algorithm_errors: dict[str, str] = {}
    algorithm_adapters = []
    for scheme_id, algorithm in site_config.get("algorithms", {}).items():
        try:
            algorithm_adapters.append(PythonAlgorithmAdapter(
                scheme_id, algorithm["factory"], algorithm.get("config", {})
            ))
        except (AlgorithmLoadError, KeyError) as exc:
            algorithm_errors[scheme_id] = str(exc)
    algorithms = AlgorithmRegistry(algorithm_adapters)

    lsf_config = site_config["lsf"]
    scratch_config = site_config["scratch"]
    lsf_settings = LsfSettings(
        queue=lsf_config.get("queue"), project=lsf_config.get("project"),
        resource=lsf_config.get("resource"), application=lsf_config.get("application"),
        array_concurrency=lsf_config.get("arrayConcurrency"),
        extra_args=tuple(lsf_config.get("extraArgs", ())),
        submit_timeout_seconds=float(lsf_config.get("submitTimeoutSeconds", 120)),
        scratch_root=str(scratch_config.get("root", "/SCRATCH")),
        scratch_user=scratch_config.get("user"),
    )
    scheduler_settings = {
        "queue": lsf_settings.queue, "project": lsf_settings.project,
        "resource": lsf_settings.resource, "application": lsf_settings.application,
        "array_concurrency": lsf_settings.array_concurrency,
        "extra_args": lsf_settings.extra_args,
        "submit_timeout_seconds": lsf_settings.submit_timeout_seconds,
        "scratch_root": lsf_settings.scratch_root,
        "scratch_user": lsf_settings.scratch_user,
    }
    scheduler = LsfScheduler(
        lsf_settings, resolved_settings.data_dir / "submission-receipts",
        dry_run=bool(lsf_config.get("dryRun", True)),
    )
    snapshot_config = site_config.get("snapshot", {})
    snapshot_reader = None
    if snapshot_config.get("path") and snapshot_config.get("columns"):
        snapshot_reader = ParquetSchedulerSnapshot(
            Path(snapshot_config["path"]), SnapshotColumns(**snapshot_config["columns"])
        )
    postprocess_config = site_config.get("postprocess", {})
    hspice = site_config["hspice"]
    measure_parser = HspiceMeasureParser(hspice["initialFields"], hspice["resultFields"])
    postprocess = (
        HspiceMtPostprocess(
            postprocess_config["command"], measure_parser,
            postprocess_config.get("mtFilePattern", "*.mt*"),
        )
        if postprocess_config.get("command") else None
    )
    coordinator = None
    if algorithms.scheme_ids and postprocess is not None:
        coordinator = WorkflowCoordinator(
            resolved_settings.database_path,
            resolved_settings.workspace_dir,
            algorithms,
            HspiceSimulator(site_config["hspice"].get("pvtParameterNames")),
            scheduler,
            postprocess,
            site_config["hspice"]["simulatorCommand"],
            site_config["hspice"].get("expectedFiles", ()),
            scheduler_settings,
            snapshot_reader,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        application.state.shutdown_requested.clear()
        resolved_settings.workspace_dir.mkdir(parents=True, exist_ok=True)
        migrate(resolved_settings.database_path)
        supervisor = None
        monitor_worker = None
        monitor_store = SqliteStatusMonitorStore(
            resolved_settings.database_path,
            on_observation=coordinator.handle_attempt_observation if coordinator else None,
        )
        monitor = StatusMonitor(
            monitor_store,
            interval_seconds=600,
            chunk_size=250,
            lease_seconds=120,
            retry_seconds=600,
            snapshot_reader=snapshot_reader,
            snapshot_identity_store=SqliteSnapshotIdentityStore(resolved_settings.database_path),
            active_job_names=monitor_store.active_job_names,
            snapshot_sink=monitor_store.record_snapshot,
            health_sink=SqliteRuntimeHealthSink(resolved_settings.database_path),
        )
        # This is a wake-up delay, not a busy scan. Due-at timestamps keep each
        # submitted Attempt on the roughly ten-minute inspection cadence.
        monitor_worker = StatusMonitorWorker(monitor, tick_seconds=10)
        monitor_worker.start()
        application.state.status_monitor_worker = monitor_worker
        handlers = {"retry_copyback": lambda payload: recovery_service.retry_copyback(payload["attemptId"])}
        lanes = [WorkerLaneConfig("recovery", ("retry_copyback",), concurrency=1)]
        recovery_service.queue.recover_abandoned()
        if coordinator is not None:
            coordinator.recover_interrupted()
            handlers.update(coordinator.handlers)
            lanes.extend(RuntimeWorkerSupervisor.workflow_defaults())
        supervisor = RuntimeWorkerSupervisor(
            resolved_settings.database_path, handlers, lanes,
        )
        supervisor.start()
        application.state.worker_supervisor = supervisor
        try:
            yield
        finally:
            if monitor_worker is not None and not monitor_worker.stop(10):
                logging.error("status monitor did not stop within the drain timeout")
            drain_timeout = max(
                60.0,
                lsf_settings.submit_timeout_seconds + 15.0,
                float(scratch_config.get("sshTimeoutSeconds", 300)) + 15.0,
            )
            if supervisor is not None and not supervisor.stop(drain_timeout):
                logging.error("one or more Backend workers did not stop within the drain timeout")

    application = FastAPI(
        title="FlowPilot Backend",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.shutdown_requested = threading.Event()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "null"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )
    repository = QueryRepository(resolved_settings.database_path)
    dataset_service = DatasetService(
        resolved_settings.database_path, resolved_settings.data_dir / "input-snapshots", measure_parser
    )
    run_service = RunService(resolved_settings.database_path, resolved_settings.workspace_dir)
    recovery_service = CaseRecoveryService(
        resolved_settings.database_path,
        site_config["hspice"]["simulatorCommand"],
        site_config["hspice"].get("expectedFiles", ()),
        scheduler_settings,
        SshCopyBackAdapter(
            ssh_program=str(scratch_config.get("sshProgram", "ssh")),
            timeout_seconds=int(scratch_config.get("sshTimeoutSeconds", 300)),
        ),
    )

    @application.get("/api/v1/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "schemaVersion": SCHEMA_VERSION,
            "databasePath": str(resolved_settings.database_path),
        }

    @application.get("/api/v1/runtime/status")
    def runtime_status() -> dict:
        result = repository.runtime_status()
        supervisor = getattr(application.state, "worker_supervisor", None)
        if supervisor is not None:
            result["workers"] = supervisor.health()
        monitor_worker = getattr(application.state, "status_monitor_worker", None)
        if monitor_worker is not None:
            result["checker"] = monitor_worker.health()
        result["backend"]["configured"] = coordinator is not None
        return result

    @application.get("/api/v1/ui/snapshot")
    def ui_snapshot() -> dict:
        snapshot = repository.ui_snapshot()
        snapshot["runtime"] = runtime_status()
        return snapshot

    @application.get("/api/v1/events")
    async def events(
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer")

        async def stream():
            nonlocal cursor
            heartbeat_at = asyncio.get_running_loop().time()
            while (
                not application.state.shutdown_requested.is_set()
                and not await request.is_disconnected()
            ):
                pending = await asyncio.to_thread(repository.list_events, cursor, 200)
                if pending:
                    for event in pending:
                        cursor = event["id"]
                        yield (
                            f"id: {cursor}\n"
                            "event: state\n"
                            f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                        )
                    heartbeat_at = asyncio.get_running_loop().time()
                    continue
                now = asyncio.get_running_loop().time()
                if now - heartbeat_at >= 15:
                    yield f": heartbeat {repository.latest_revision()}\n\n"
                    heartbeat_at = now
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.get("/api/v1/capabilities")
    def capabilities() -> dict:
        return {
            "implemented": [
                "health", "database_schema", "dataset_scan", "input_snapshots",
                "run_lifecycle", "durable_work_queue", "hspice_param_render",
                "hspice_measure_parse", "batch_manifest", "lsf_submit_adapter",
                "parquet_snapshot_adapter", "postprocess_adapter",
                "ui_snapshot", "server_sent_events", "runtime_supervisor",
                "bounded_status_monitor", "scratch_runner", "copyback_recovery",
            ],
            "adapters": {
                "algorithm": "site_implementation_required",
                "measureParser": "reference_implemented",
                "simulator": "reference_implemented",
                "scheduler": "reference_implemented",
                "schedulerSnapshot": "reference_implemented",
                "postprocess": "reference_implemented",
            },
        }

    @application.get("/api/v1/integration-checks")
    def integration_checks() -> dict:
        checks = validate_site_config(site_config)
        checks.extend(
            {"name": f"algorithm:{name}", "status": "error", "detail": error}
            for name, error in algorithm_errors.items()
        )
        return {"ready": coordinator is not None and not algorithm_errors, "items": checks}

    @application.get("/api/v1/algorithms")
    def list_algorithms() -> dict:
        return {
            "items": [
                {"id": scheme_id, "stages": algorithms.get(scheme_id).stage_definitions()}
                for scheme_id in algorithms.scheme_ids
            ],
            "errors": algorithm_errors,
        }

    @application.get("/api/v1/datasets")
    def list_datasets() -> dict:
        return {"items": repository.list_datasets()}

    @application.post("/api/v1/datasets")
    def create_dataset(
        request: CreateDatasetRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        defaults = site_config["dataset"]
        supplied = request.convention or DatasetConventionRequest()
        convention = DatasetConvention(
            sp_pattern=supplied.spPattern or defaults["spPattern"],
            mt_template=supplied.mtTemplate or defaults["mtTemplate"],
            recursive=supplied.recursive if supplied.recursive is not None else defaults["recursive"],
        )
        dataset_id = _stable_id("ds", idempotency_key, "create_dataset")
        try:
            dataset_service.create(request.name, Path(request.path), convention, dataset_id)
        except (DatasetScanError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"datasetId": dataset_id}

    @application.post("/api/v1/datasets/{dataset_id}/scan")
    def scan_dataset(
        dataset_id: str,
        request: ScanDatasetRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        scan_id = _stable_id("scan", idempotency_key, dataset_id)
        try:
            return dataset_service.scan(dataset_id, request.mode, scan_id)
        except DatasetScanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/v1/datasets/{dataset_id}/corners")
    def list_dataset_corners(dataset_id: str) -> dict:
        return {"items": repository.list_dataset_corners(dataset_id)}

    @application.get("/api/v1/runs")
    def list_runs() -> dict:
        return {"items": repository.list_runs()}

    @application.post("/api/v1/runs")
    def create_run(
        request: CreateRunRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        if request.algorithmScheme not in algorithms.scheme_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Algorithm scheme is not available: {request.algorithmScheme}",
            )
        try:
            result, replayed = run_service.create(
                idempotency_key, dataset_id=request.datasetId, name=request.name,
                pvt=request.pvt, algorithm_scheme=request.algorithmScheme,
                algorithm_config=request.algorithmConfig, corner_numbers=request.cornerNumbers,
            )
        except (RunServiceError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**result, "replayed": replayed}

    @application.get("/api/v1/runs/{run_id}/corners")
    def list_run_corners(run_id: str) -> dict:
        return {"items": repository.list_run_corners(run_id)}

    @application.post("/api/v1/runs/{run_id}/corners")
    def add_run_corners(
        run_id: str,
        request: AddCornersRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        try:
            result, replayed = run_service.add_corners(idempotency_key, run_id, request.cornerNumbers)
        except (RunServiceError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**result, "replayed": replayed}

    @application.post("/api/v1/runs/{run_id}/pause")
    def set_run_paused(
        run_id: str,
        request: PauseRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        try:
            result, replayed = run_service.set_paused(idempotency_key, run_id, request.paused)
        except (RunServiceError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**result, "replayed": replayed}

    @application.post("/api/v1/runs/{run_id}/case-recovery/preview")
    def preview_case_recovery(run_id: str, request: RecoveryPreviewRequest) -> dict:
        try:
            return recovery_service.preview(run_id, request.action, request.filters.to_domain())
        except RecoveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/v1/case-recovery/confirm")
    def confirm_case_recovery(
        request: RecoveryConfirmRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        try:
            result, replayed = recovery_service.confirm(request.token, idempotency_key)
        except RecoveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**result, "replayed": replayed}

    @application.post("/api/v1/execution-attempts/{attempt_id}/copyback", status_code=202)
    def retry_copyback(
        attempt_id: str,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        try:
            recovery_service.validate_copyback_request(attempt_id)
        except RecoveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        work_id = recovery_service.queue.enqueue(
            f"copyback:{attempt_id}:{idempotency_key}",
            "retry_copyback", {"attemptId": attempt_id},
        )
        return {"attemptId": attempt_id, "status": "queued", "workId": work_id}

    return application


app = create_app()
