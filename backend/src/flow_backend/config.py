from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    workspace_dir: Path
    site_config_path: Path | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        data_dir = Path(
            os.environ.get("FLOWPILOT_DATA_DIR", Path.home() / ".flowpilot")
        ).expanduser()
        return cls(
            data_dir=data_dir,
            database_path=Path(
                os.environ.get("FLOWPILOT_DATABASE_PATH", data_dir / "flowpilot.sqlite3")
            ).expanduser(),
            workspace_dir=Path(
                os.environ.get("FLOWPILOT_WORKSPACE_DIR", data_dir / "runs")
            ).expanduser(),
            site_config_path=(
                Path(os.environ["FLOWPILOT_SITE_CONFIG"]).expanduser()
                if os.environ.get("FLOWPILOT_SITE_CONFIG") else None
            ),
        )

