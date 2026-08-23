from pathlib import Path
import json

from fastapi.testclient import TestClient

from flow_backend.api import create_app
from flow_backend.config import Settings


def test_health_and_empty_collections(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "flowpilot.sqlite3",
        workspace_dir=tmp_path / "runs",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/api/v1/datasets").json() == {"items": []}
        assert client.get("/api/v1/runs").json() == {"items": []}
        snapshot = client.get("/api/v1/ui/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["datasets"] == []
        assert snapshot.json()["runs"] == []
        assert snapshot.json()["revision"] == 0
        runtime = client.get("/api/v1/runtime/status").json()
        assert runtime["backend"]["state"] == "ready"
        assert runtime["checker"]["state"] in {"normal", "checking"}


def test_dataset_api_scans_without_exposing_file_contents(tmp_path: Path) -> None:
    source = tmp_path / "private-input"
    source.mkdir()
    (source / "base.sp1").write_text("title\n.param rangeselcode=1\n.end\n", encoding="utf-8")
    (source / "base.mt1").write_text("rangesel loss0\n1 0.25\n", encoding="utf-8")
    site = tmp_path / "site.json"
    site.write_text(json.dumps({
        "hspice": {
            "initialFields": {"rangesel": "rangesel"},
            "resultFields": {"loss_0": "loss0"},
        }
    }), encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "flowpilot.sqlite3",
        workspace_dir=tmp_path / "runs",
        site_config_path=site,
    )
    app = create_app(settings)
    headers = {"Idempotency-Key": "create-private-dataset"}
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/datasets", headers=headers,
            json={"name": "Private", "path": str(source)},
        )
        assert created.status_code == 200
        dataset_id = created.json()["datasetId"]
        scan = client.post(
            f"/api/v1/datasets/{dataset_id}/scan",
            headers={"Idempotency-Key": "scan-private-dataset"},
            json={"mode": "append"},
        )
        assert scan.status_code == 200
        assert scan.json()["new"] == 1
        corners = client.get(f"/api/v1/datasets/{dataset_id}/corners").json()["items"]
        assert corners[0]["availability"] == "available"
        assert corners[0]["inputVersion"]["initialCode"] == {"rangesel": 1.0}
        snapshot = client.get("/api/v1/ui/snapshot").json()
        assert snapshot["revision"] > 0
        assert snapshot["datasets"][0]["id"] == dataset_id
