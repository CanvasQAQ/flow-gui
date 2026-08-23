# FlowPilot Backend

This directory contains the local Python process that owns workflow state. It
includes a complete adapter-driven reference workflow that can be validated with
synthetic data before site-specific HSPICE, LSF, algorithm and postprocess code
is available.

## Local development

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install '.[dev,parquet]'
.venv/bin/flow-backend
```

The API listens on `http://127.0.0.1:8765`. Interactive API documentation is at
`/docs`.

Override local storage with `FLOWPILOT_DATA_DIR`. The default is
`~/.flowpilot`; production Electron startup should set this explicitly to the
application data directory.

## Implemented

Implemented:

- repeatable SQLite migrations and durable work queue;
- Dataset scanning, immutable SP/MT input versions and per-Corner error isolation;
- HSPICE `.PARAM` rendering and traditional measure-table parsing;
- Run/Corner/Stage/Algorithm/Testcase/Attempt/Batch/Postprocess state and history;
- immutable JSON Batch manifests and compute-node Runner;
- safe `bsub` argument construction, receipt persistence and Parquet observation;
- evidence-based reconciliation and restart-safe submission behavior;
- algorithm plugin contract and dynamic Stage graph;
- Case Recovery preview/confirm, ignore and partial resubmit;
- debug Rollback preview/execute via `flow-admin`;
- local API, idempotent mutations and integration checks.

## Site work still required

- fill in [site.example.json](config/site.example.json);
- install the private algorithm package and configure its factories;
- verify MT column names and any local HSPICE format differences;
- map the real public Parquet columns;
- configure the existing Postprocess command and its generated MT filename
  pattern, then verify the MT result-field mapping;
- verify permissions and the Runner environment on an LSF execution node.

Start with [Integration contracts](docs/INTEGRATION_CONTRACTS.md), then follow the
[real-machine checklist](docs/REAL_MACHINE_CHECKLIST.md). The internal design and
recovery rules are summarized in [Backend architecture](docs/ARCHITECTURE.md).
Frontend integration uses the [local API contract](docs/API.md).

For an engineer or coding agent doing the first confidential-machine bring-up,
use [On-machine agent guide](docs/ON_MACHINE_AGENT_GUIDE.md) as the entry point.
It maps each fact that must be collected on the machine to the exact config,
adapter, test, and acceptance step that may need to change. Do not begin by
editing the Workflow coordinator.

Private algorithms that provide Results charts must follow the
[Algorithm presentation contract](docs/ALGORITHM_PRESENTATION_CONTRACT.md),
including its all-Rounds/single-Round behavior and name-mapping rules.

The runtime isolates scheduler submission, workflow decisions, and postprocess
into separate durable lanes. Submitted Attempts are checked from their exact
Case `status.json` paths on a 600-second due-time cadence in bounded chunks;
the public scheduler snapshot is auxiliary evidence and is queried only when
its identity changes.

LSF items stage only their SP input into
`/SCRATCH/<user>/flowpilot/<attempt_id>`, run there, and copy the complete
Scratch directory back into an Attempt-specific network return directory.
Copy-back recovery uses the zero-byte hostname marker created by the LSF child
and ordinary SSH. Scratch and SSH options live under `scratch` in
`config/site.example.json`; host keys and authentication require real-cluster
verification.
