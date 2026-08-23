# Local API contract

Base URL: `http://127.0.0.1:8765/api/v1`

All mutation requests require `Idempotency-Key`. Repeating the same key and body
returns the original result; reusing a key for a different request is rejected.
FastAPI also serves the generated OpenAPI UI at `/docs`.

## Runtime and integration

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Backend and schema version |
| GET | `/capabilities` | Implemented reference capabilities |
| GET | `/integration-checks` | Site commands, snapshot and algorithm readiness |
| GET | `/algorithms` | Configured schemes and dynamic Stage definitions |
| GET | `/runtime/status` | Backend worker and system-level status-check health |
| GET | `/ui/snapshot` | Revisioned Dataset/Run/Corner/Recovery read model |
| GET | `/events` | SSE change notifications; supports `Last-Event-ID` |

`/algorithms` currently returns Stage definitions supplied by the configured
Python packages. The API does **not** yet expose the per-Stage presentation data
needed by algorithm-specific charts. `/ui/snapshot` contains a compact workflow
read model, not the complete algorithm-call/Testcase result history. See the
algorithm presentation section of `INTEGRATION_CONTRACTS.md` before adding a
Results-tab integration. Its normative detail endpoint, Round aggregation, and
name-mapping requirements are in `ALGORITHM_PRESENTATION_CONTRACT.md`.

`integration-checks.ready` currently reports whether the coordinator and
algorithm registry were constructed; clients must also require every mandatory
item in `items` to be `ok`. Keep `lsf.dryRun=true` during bring-up because this
endpoint is not yet enforced as a submission gate.

## Dataset

```text
GET  /datasets
POST /datasets
POST /datasets/{datasetId}/scan
GET  /datasets/{datasetId}/corners
```

Create body:

```json
{
  "name": "baseline",
  "path": "/project/input",
  "convention": {
    "spPattern": "^(?P<stem>.+)\\.sp(?P<corner>[^/]*)$",
    "mtTemplate": "{stem}.mt{corner}",
    "recursive": false
  }
}
```

Scan body is `{"mode":"append"}` or `{"mode":"overwrite"}`. Dataset scanning
is intentionally explicit; creating a Dataset does not start a scan.

## Run

```text
GET  /runs
POST /runs
GET  /runs/{runId}/corners
POST /runs/{runId}/corners
POST /runs/{runId}/pause
```

Create body:

```json
{
  "datasetId": "ds_...",
  "name": "nominal",
  "pvt": {"vdd": 0.72, "vcm": 0.36},
  "algorithmScheme": "adaptive-a",
  "algorithmConfig": {},
  "cornerNumbers": [1, 2, 3]
}
```

Adding Corners uses `{"cornerNumbers":[4,5]}`. Pause/resume uses
`{"paused":true}` or `{"paused":false}`. Pause only blocks new algorithm and
submission work.

## Case Recovery

Preview:

```text
POST /runs/{runId}/case-recovery/preview
```

```json
{
  "action": "resubmit",
  "filters": {
    "testcaseIds": [],
    "cornerNumbers": [1, 2],
    "stageKeys": ["search"],
    "algorithmCallNumbers": [1],
    "statuses": ["failed", "status_unknown"]
  }
}
```

The response includes risk counts, actual Testcase snapshots, grouping and an
expiring token. Confirm with a new idempotency key:

```text
POST /case-recovery/confirm
{"token":"preview_..."}
```

The Backend re-evaluates the filter before confirmation. `resubmit` creates new
Attempts and Batches while keeping fixed Case directories. `ignore` preserves
history and removes selected Testcases from the group gate.

For an Attempt in `copyback_waiting`, queue a stage-out-only retry with a new
idempotency key:

```text
POST /execution-attempts/{attemptId}/copyback
```

The response is `202` with a durable Work ID. Hostname and paths are resolved
from the current Attempt and its zero-byte hostname marker; the UI cannot
supply an SSH host, source, destination, or command.

## Debug Rollback

Rollback is intentionally not exposed through the GUI API:

```bash
flow-admin rollback --run run_id --corners 1,2,5-10 --stage search
flow-admin rollback --run run_id --corners 1,2,5-10 --stage search \
  --execute --idempotency-key approved-debug-operation
```

The first command is preview-only. Execution skips Corners with downstream
PEND/RUN/status-unknown Attempts and keeps superseded history.
