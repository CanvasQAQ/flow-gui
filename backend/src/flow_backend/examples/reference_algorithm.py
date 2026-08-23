from typing import Any


class ReferenceAlgorithm:
    """Contract example only; this is not a production search algorithm."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def stage_definitions(self):
        return [
            {"key": "search", "name": "Reference search", "final": False},
            {"key": "final", "name": "Final validation", "final": True},
        ]

    def decide(self, stage_key: str, payload: dict[str, Any]):
        verified = [item for item in payload["verifiedTestcases"] if item["result"] is not None]
        if stage_key == "search" and not verified:
            parameter_names = self.config.get("parameterNames", {})
            initial = payload["corner"]["initialCode"]
            parameters = {
                parameter_names.get(logical_name, logical_name): value
                for logical_name, value in initial.items()
            }
            return {
                "kind": "add_testcases",
                "reason": "Synthetic contract example: validate the initial Code once",
                "testcases": [{"name": "initial_code", "parameters": parameters}],
            }
        if stage_key == "search":
            best = min(
                verified,
                key=lambda item: max(value for key, value in item["result"].items() if key.startswith("loss_")),
            )
            return {
                "kind": "advance_stage",
                "reason": "Synthetic contract example: choose minimum worst-phase Loss",
                "result": {"selectedParameters": best["parameters"], "metrics": best["result"]},
                "nextStage": "final",
            }
        if not verified:
            previous = payload["previousStageResults"][-1]["result"]
            return {
                "kind": "add_testcases",
                "reason": "Validate the complete parameter set selected by the previous Stage",
                "testcases": [{
                    "name": "final_validation",
                    "parameters": previous["selectedParameters"],
                }],
            }
        return {
            "kind": "complete",
            "reason": "Synthetic final validation completed",
            "result": {"parameters": verified[-1]["parameters"], "metrics": verified[-1]["result"]},
        }


def create_algorithm(config: dict[str, Any]) -> ReferenceAlgorithm:
    return ReferenceAlgorithm(config)

