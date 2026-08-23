# On-machine agent guide

This is the entry point for a coding agent working on the confidential
LSF/HSPICE machine. Its purpose is to answer, in order: what must be learned on
that machine, what may be changed, how to prove the change, and what must not be
copied out.

Read these files before editing code:

1. `INTEGRATION_CONTRACTS.md` -- stable boundaries and safety rules;
2. `REAL_MACHINE_CHECKLIST.md` -- acceptance order;
3. `../config/site.example.json` -- all expected site-owned values;
4. `ARCHITECTURE.md` -- workflow ownership and recovery invariants;
5. `API.md` -- the renderer/Backend boundary.
6. `ALGORITHM_PRESENTATION_CONTRACT.md` -- the required Results schema, Round
   semantics, and physical/logical/display naming layers.

## Definition of done

Bring-up is complete only when a site-owned config is installed, the private
conformance fixtures pass, every mandatory integration check has been reviewed,
the dry-run acceptance is recorded, and the limited live acceptance passes in
a dedicated Queue/project. A green local unit-test run alone is not completion.

Never commit netlists, measure values, waveforms, credentials, hostnames,
absolute confidential paths, `known_hosts`, scheduler receipts, Parquet rows,
or the site-owned config. Commit only adapters, schemas, sanitized fixtures, and
tests that assert shapes/classifications rather than confidential values.

## First 15 minutes

1. Record the current commit, Python version, HSPICE version, LSF version, user,
   Backend host, execution-node environment, and filesystem mount points in a
   private bring-up log.
2. Create a Python 3.11+ environment and install `flow-backend[parquet]` plus
   the private algorithm package.
3. Copy `backend/config/site.example.json` to a path outside the repository.
   Confirm that `lsf.dryRun` is still `true`.
4. Export `FLOWPILOT_SITE_CONFIG`, `FLOWPILOT_DATA_DIR`, and
   `FLOWPILOT_WORKSPACE_DIR`. Use durable local storage for the database and the
   approved simulation filesystem for the workspace.
5. Start `flow-backend`, open `/api/v1/integration-checks`, and record every
   `pending`/`error`. Do not treat the top-level `ready` value as approval: the
   current implementation does not enforce every item or block live submit.
6. Build the private fixture set listed in `REAL_MACHINE_CHECKLIST.md`. Keep it
   outside Git.

## Change map

| Fact learned on the machine | Prefer changing | Add/execute proof | Do not change first |
| --- | --- | --- | --- |
| SP/MT naming and recursion | Site config `dataset` | Private append/overwrite scan fixture | Workflow/coordinator |
| Code/PVT parameter names | Site config `hspice.initialFields`, `resultFields`, `pvtParameterNames` | Renderer/parser conformance test | Generic state machine |
| Duplicate `.PARAM` semantics | New site `SimulatorAdapter` behind `ports.py` | Fixture showing the authoritative occurrence only | Disable duplicate checks globally |
| MT CSV or multi-row format | New `MeasureParser` implementation and adapter selection | Normal, failed, missing, multi-row fixtures | Feed CSV into the whitespace parser |
| Algorithm Stage/decision rules | Private Python package factory plus `algorithms` config | Contract tests for every decision kind and Stage transition | Embed algorithm logic in Backend or React |
| Queue/project/resources/Array limits | Site config `lsf` | Inspect dry-run manifest and argument array | Turn off dry-run |
| Public Parquet schema/states | Site config `snapshot.columns`; site reader only if semantics differ | Snapshot fixture covering Job/Array and all observed states | Query Parquet from the UI |
| Scratch/user/Runner availability | Site config `scratch` and deployment wrapper if needed | Interactive Runner execution on an execution node | Guess cleanup time or disable host checking |
| Postprocess command/MT output | Site config `postprocess`; adapter only if command semantics differ | Representative waveform-to-MT fixture | Require Postprocess to emit application JSON |
| Algorithm-specific Results charts | Presentation contract described below, Backend detail API, frontend renderer registry | Sanitized presentation fixture plus API/component tests | Generate JSX/ECharts code in the Python package |

If a site difference fits an existing config field, do not fork an adapter. If
it changes parsing, rendering, submission, snapshot, or postprocess semantics,
implement the corresponding protocol in `backend/src/flow_backend/ports.py`,
select it from site configuration, and leave `coordinator.py` unchanged unless
the stable workflow itself has changed.

## Algorithm package contract currently available

Configure a factory as `python.module:create_algorithm`. The factory receives
JSON config and returns an object implementing:

```python
def stage_definitions() -> list[dict]: ...
def decide(stage_key: str, payload: dict) -> dict: ...
```

`decide` receives Run/PVT, Corner initial Code, current Stage identity, all
verified Testcases/results in the Stage, previous Stage results, and algorithm
config. It returns one of `add_testcases`, `advance_stage`, `complete`, or
`cannot_continue`, exactly as specified in `INTEGRATION_CONTRACTS.md`.

Validate at least: unique filesystem-safe Stage/Testcase keys, complete
Code/Mode parameters, each decision kind used by the algorithm, empty/failed
result handling, deterministic replay from recorded input, and absence of
non-JSON values.

## Algorithm-specific charts: current gap and target v1

There is no production chart plugin interface today.

- `src/components/CornerWorkflowModal.jsx` selects React components from a
  hard-coded `RESULT_RENDERERS` map and builds synthetic data locally.
- `src/components/StageEvidenceCharts.jsx` contains fixed Stage 1/2 examples.
- `backend/src/flow_backend/ports.py` exposes no presentation method.
- `/api/v1/ui/snapshot` exposes only compact Corner state/final result, not the
  recorded Stage calls and Testcase result series.

The normative target is `ALGORITHM_PRESENTATION_CONTRACT.md`. Every point
carries the Round that generated it, and a Stage may declare all-Rounds view,
exact-Round view, or both. Do not invent a different private payload during
real-machine integration.

Implement this before claiming that charts come from an algorithm package. The
recommended v1 is declarative and versioned, for example:

```json
{
  "schemaVersion": 1,
  "renderer": "code-metric",
  "title": "Parameter scan",
  "summary": [
    {"label": "Best testcase", "value": "tc_003"}
  ],
  "xAxis": {"key": "primary_code", "label": "Code", "scale": "linear"},
  "metrics": [
    {"key": "metric_0", "label": "Metric 0"}
  ],
  "points": [
    {"id": "tc_003", "round": 2, "sequence": 3, "x": 10,
     "values": {"metric_0": 0.25}}
  ]
}
```

The example intentionally uses neutral logical keys. Development aliases such
as Code, EyeLoss, Phase, `rangesel`, and `loss_0` are not generic application
contracts. Real physical names belong in the external site config/private
package; approved user-facing labels come from the presentation payload.

The precise implementation sequence is:

1. define and validate a bounded JSON schema in the Backend; reject unknown
   schema versions, non-finite numbers, oversized labels/arrays, executable
   content, and raw ECharts options;
2. extend the algorithm adapter/decision persistence so presentation data is
   reproducible from the recorded algorithm input and output;
3. add the detail endpoint
   `GET /api/v1/run-corners/{runCornerId}/results` returning Stage
   definitions, recorded calls, sanitized Testcase results, and presentation;
4. add a frontend registry keyed by `renderer`, with a trusted `code-metric@1`
   component supporting Line/Table and Stage/Round switches, plus an `unknown`
   table fallback;
5. replace hard-coded `STAGES`, algorithm display-name matching, and synthetic
   `buildResultData`/`getTestcases` data in the active Corner modal;
6. test an algorithm with different Stage keys, an unsupported renderer, a
   schema-version mismatch, empty/partial results, and a large bounded series.

Ownership is deliberately split: the private algorithm package owns chart
semantics, labels, series/table data, and summary values; the application owns
trusted React renderers, colors, accessibility, limits, and escaping. If a site
needs a genuinely new visual grammar, add a reviewed renderer to the packaged
frontend rather than loading executable UI code from the Python package.

## Required proof before live submission

Run the repository Backend tests, then the private conformance tests. Complete
all Dry-run Acceptance steps in `REAL_MACHINE_CHECKLIST.md` and attach sanitized
evidence to the private bring-up log. Only then ask the site owner to approve a
dedicated test Queue/project and change `lsf.dryRun` to `false` in the external
site config.

After the limited live acceptance, restore `dryRun=true` unless the production
gate has been explicitly approved. Report every repository change by contract
area, the fixture that proves it, the remaining site assumption, and a rollback
procedure.
