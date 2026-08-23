from pathlib import Path
from typing import Any, Sequence
import glob
import subprocess

from ..ports import MeasureParser


class PostprocessError(RuntimeError):
    pass


class HspiceMtPostprocess:
    """Run the existing waveform script, then parse the MT files it produced."""

    def __init__(
        self, command: Sequence[str], parser: MeasureParser,
        mt_file_pattern: str = "*.mt*",
    ):
        if not command:
            raise ValueError("postprocess command must not be empty")
        self.command = tuple(command)
        self.parser = parser
        self.mt_file_pattern = mt_file_pattern

    def run(self, stage_directory: Path, config: dict[str, Any]) -> dict[str, Any]:
        replacements = {
            "{stage_path}": str(stage_directory),
            "{config_path}": str(config.get("configPath", "")),
        }
        command = [replacements.get(token, token) for token in self.command]
        completed = subprocess.run(
            command, cwd=stage_directory, text=True, capture_output=True, check=False
        )
        (stage_directory / "postprocess.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (stage_directory / "postprocess.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise PostprocessError(f"postprocess failed with exit code {completed.returncode}")
        testcase_results = {}
        for testcase in config.get("testcases", []):
            case_path = Path(testcase["casePath"])
            matches = sorted(Path(path) for path in glob.glob(str(case_path / self.mt_file_pattern)))
            if len(matches) != 1:
                raise PostprocessError(
                    f"expected exactly one MT file for {testcase['name']!r} using "
                    f"{self.mt_file_pattern!r}, found {len(matches)}"
                )
            testcase_results[testcase["id"]] = self.parser.parse_result(matches[0])
        return {
            "status": "success",
            "metrics": {"processed": len(testcase_results)},
            "testcases": testcase_results,
            "files": [],
        }
