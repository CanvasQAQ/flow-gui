from importlib import import_module
from typing import Any, Mapping, Sequence

from ..workflow import AlgorithmDecision, TestcaseProposal


class AlgorithmLoadError(RuntimeError):
    pass


class PythonAlgorithmAdapter:
    """Load a site algorithm factory from `python.module:factory_name`."""

    def __init__(self, scheme_id: str, factory: str, config: Mapping[str, Any] | None = None):
        self._scheme_id = scheme_id
        self.factory_path = factory
        self.config = dict(config or {})
        try:
            module_name, factory_name = factory.split(":", 1)
            factory_callable = getattr(import_module(module_name), factory_name)
            self.implementation = factory_callable(self.config)
        except Exception as exc:
            raise AlgorithmLoadError(f"could not load algorithm {scheme_id!r} from {factory!r}: {exc}") from exc

    @property
    def scheme_id(self) -> str:
        return self._scheme_id

    def stage_definitions(self) -> Sequence[dict[str, Any]]:
        stages = self.implementation.stage_definitions()
        if not stages or any("key" not in stage for stage in stages):
            raise AlgorithmLoadError("algorithm must return a non-empty Stage list with unique keys")
        keys = [stage["key"] for stage in stages]
        if len(keys) != len(set(keys)):
            raise AlgorithmLoadError("algorithm returned duplicate Stage keys")
        return stages

    def decide(self, stage_key: str, payload: dict[str, Any]) -> AlgorithmDecision:
        raw = self.implementation.decide(stage_key, payload)
        if isinstance(raw, AlgorithmDecision):
            decision = raw
        elif isinstance(raw, dict):
            decision = AlgorithmDecision(
                kind=raw["kind"],
                reason=raw.get("reason", ""),
                testcases=tuple(
                    TestcaseProposal(
                        name=item["name"], parameters=item["parameters"], reason=item.get("reason", "")
                    )
                    for item in raw.get("testcases", ())
                ),
                result=raw.get("result"),
                next_stage=raw.get("nextStage") or raw.get("next_stage"),
            )
        else:
            raise AlgorithmLoadError("algorithm decide() must return a dict or AlgorithmDecision")
        decision.validate([stage["key"] for stage in self.stage_definitions()])
        return decision


class AlgorithmRegistry:
    def __init__(self, adapters: Sequence[PythonAlgorithmAdapter] = ()):
        self._adapters = {adapter.scheme_id: adapter for adapter in adapters}

    def get(self, scheme_id: str):
        try:
            return self._adapters[scheme_id]
        except KeyError as exc:
            raise AlgorithmLoadError(f"algorithm scheme is not configured: {scheme_id}") from exc

    @property
    def scheme_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
