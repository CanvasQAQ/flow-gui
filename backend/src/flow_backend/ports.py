"""Interfaces that isolate undecided or site-specific integrations."""

from pathlib import Path
from typing import Any, Protocol, Sequence


class AlgorithmAdapter(Protocol):
    @property
    def scheme_id(self) -> str: ...

    def stage_definitions(self) -> Sequence[dict[str, Any]]: ...

    def decide(self, stage_key: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class SimulatorAdapter(Protocol):
    def render_input(
        self,
        source_sp: Path,
        destination: Path,
        pvt: dict[str, Any],
        parameters: dict[str, Any],
    ) -> None: ...


class SchedulerAdapter(Protocol):
    def submit(self, manifest_path: Path, job_name: str, settings: dict[str, Any]) -> str: ...

    def cancel(self, job_id: str) -> None: ...


class SchedulerSnapshotReader(Protocol):
    def snapshot_identity(self) -> str | None: ...

    def read_jobs(self, job_names: Sequence[str]) -> Sequence[dict[str, Any]]: ...


class PostprocessAdapter(Protocol):
    def run(self, stage_directory: Path, config: dict[str, Any]) -> dict[str, Any]: ...


class MeasureParser(Protocol):
    def parse_initial(self, mt_path: Path) -> dict[str, Any]: ...

    def parse_result(self, mt_path: Path) -> dict[str, Any]: ...

