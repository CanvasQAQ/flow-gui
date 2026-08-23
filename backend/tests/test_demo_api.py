from fastapi.testclient import TestClient

from flow_backend.demo_api import create_demo_app


def test_demo_snapshot_is_deterministic_and_marked_as_demo():
    with TestClient(create_demo_app("default")) as client:
        response = client.get("/api/v1/ui/snapshot")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["runtime"]["backend"]["mode"] == "demo"
        assert snapshot["runtime"]["backend"]["scenario"] == "default"
        assert len(snapshot["runs"][0]["corners"]) == 1248
        assert snapshot["runs"][0]["corners"][0]["state"] == "done"


def test_demo_mutations_update_snapshot():
    with TestClient(create_demo_app("empty")) as client:
        headers = {"Idempotency-Key": "dataset-test"}
        created = client.post(
            "/api/v1/datasets", headers=headers,
            json={"name": "Demo input", "path": "/demo/input"},
        ).json()
        dataset_id = created["datasetId"]
        run = client.post(
            "/api/v1/runs", headers={"Idempotency-Key": "run-test"},
            json={
                "datasetId": dataset_id, "name": "Demo run",
                "pvt": {"vdd": "0.72", "vcm": "0.36"},
                "algorithmScheme": "reference-search", "cornerNumbers": [1, 2, 3],
            },
        ).json()
        client.post(
            f"/api/v1/runs/{run['runId']}/pause",
            headers={"Idempotency-Key": "pause-test"}, json={"paused": True},
        )
        snapshot = client.get("/api/v1/ui/snapshot").json()
        assert snapshot["revision"] == 4
        assert snapshot["runs"][0]["status"] == "paused"
        assert len(snapshot["runs"][0]["corners"]) == 3


def test_failure_scenario_supports_recovery():
    with TestClient(create_demo_app("failures")) as client:
        snapshot = client.get("/api/v1/ui/snapshot").json()
        run = snapshot["runs"][0]
        testcase_id = run["recoveryCases"][0]["testcaseId"]
        preview = client.post(
            f"/api/v1/runs/{run['id']}/case-recovery/preview",
            json={"action": "resubmit", "filters": {"testcaseIds": [testcase_id]}},
        ).json()
        confirmed = client.post(
            "/api/v1/case-recovery/confirm",
            headers={"Idempotency-Key": "recover-test"}, json={"token": preview["token"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["affected"] == 1
