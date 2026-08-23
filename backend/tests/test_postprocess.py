from pathlib import Path
import sys

from flow_backend.adapters.postprocess import HspiceMtPostprocess


class RecordingParser:
    def __init__(self):
        self.paths = []

    def parse_result(self, path: Path):
        self.paths.append(path)
        return {"loss_0": 0.125}


def test_postprocess_script_creates_mt_then_adapter_parses_it(tmp_path: Path) -> None:
    case = tmp_path / "case_001"
    case.mkdir()
    parser = RecordingParser()
    adapter = HspiceMtPostprocess(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('case_001/result.mt0').write_text('generated')",
        ],
        parser,
        "result.mt0",
    )

    result = adapter.run(
        tmp_path,
        {"testcases": [{"id": "tc1", "name": "case_001", "casePath": str(case)}]},
    )

    assert result["testcases"] == {"tc1": {"loss_0": 0.125}}
    assert parser.paths == [case / "result.mt0"]
