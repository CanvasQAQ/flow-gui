# Algorithm result presentation contract

Status: target contract for implementation. The current Backend and frontend do
not yet implement this document end to end.

This contract lets a private algorithm package describe Code/metric results
without shipping executable UI code. It supports a Stage-wide view containing
all algorithm rounds, a single-Round view, line charts, complete data tables,
multiple metric/Phase series, and semantic highlighting of selected points.

Normative terms `MUST`, `SHOULD`, and `MAY` describe requirements for future
algorithm packages, the Backend adapter/API, and frontend renderers.

## 1. Ownership boundary

- The algorithm package MUST own result meaning, stable field keys, display
  labels, Stage/Round membership, ordering, and special-point semantics.
- The Backend MUST validate, version, persist, and serve presentation snapshots.
- The frontend MUST own trusted rendering code, layout, colors, accessibility,
  escaping, and size limits.
- An algorithm package MUST NOT return JSX, JavaScript, HTML, formatter source,
  callbacks, or raw ECharts options.
- Unknown renderers and schema versions MUST fall back to a safe table or an
  explicit unsupported-version message; they MUST NOT make the Results view
  fail as a whole.

## 2. Name layers and confidentiality

The application distinguishes three names. They MUST NOT be collapsed into a
single hard-coded identifier.

| Layer | Example in development | Real-machine source | Owner |
| --- | --- | --- | --- |
| Physical/source name | `rangeselcode`, `loss_0` | Actual SP parameter or MT column | External site config/private package |
| Stable logical key | `primary_code`, `metric_phase_0` | Opaque JSON-safe identifier | Private algorithm contract |
| Display label | `Code`, `Phase 0 metric` | Approved real user-facing term | Presentation descriptor |

Physical names belong in the external site config or private algorithm package,
not in the generic Workflow or React components. Logical keys are used in
recorded JSON and API payloads. Labels are presentation only and MAY change
without changing persisted keys.

The generic application MUST NOT infer meaning from substrings such as `code`,
`loss`, `eyeloss`, `phase`, `rangesel`, or numeric suffixes. Metric order and
Phase identity come from the presentation descriptor.

Example external mapping:

```json
{
  "hspice": {
    "initialFields": {
      "primary_code": "REAL_INITIAL_CODE_COLUMN"
    },
    "resultFields": {
      "metric_phase_0": "REAL_RESULT_COLUMN_0",
      "metric_phase_1": "REAL_RESULT_COLUMN_1"
    },
    "pvtParameterNames": {
      "supply": "REAL_SUPPLY_PARAM"
    }
  },
  "algorithms": {
    "private-search": {
      "factory": "private_package.search:create_algorithm",
      "config": {
        "parameterNames": {
          "primary_code": "REAL_SP_CODE_PARAM"
        }
      }
    }
  }
}
```

This file is site-owned and MUST remain outside Git when the names themselves
are confidential.

## 3. Stage presentation descriptor

Each Stage that exposes Results SHOULD include this descriptor in
`stage_definitions()`:

```json
{
  "key": "coarse_search",
  "name": "Coarse search",
  "final": false,
  "presentation": {
    "schemaVersion": 1,
    "renderer": "code-metric",
    "defaultView": "line",
    "availableViews": ["line", "table"],
    "roundDisplay": {
      "supportedScopes": ["stage", "round"],
      "defaultScope": "stage",
      "roundLabel": "Round"
    }
  }
}
```

`renderer` is a stable capability key, not an algorithm display name. Multiple
algorithms SHOULD reuse `code-metric` when their data has the same visual
grammar. A new algorithm does not require a new React component merely because
its name or field labels differ.

Valid Round configurations are:

- `supportedScopes: ["stage"]`: always aggregate all rounds in the active Stage
  execution; appropriate for algorithms that progressively extend the Code set;
- `supportedScopes: ["round"]`: show one Round at a time;
- `supportedScopes: ["stage", "round"]`: allow an `All rounds`/`Round N` switch;
  this SHOULD be the normal default for iterative searches.

## 4. Presentation snapshot v1

The Backend calls an optional algorithm method after each recorded decision:

```python
def build_presentation(
    stage_key: str,
    payload: dict,
    decision: dict,
) -> dict | None: ...
```

The returned JSON is persisted with that algorithm call. A v1 `code-metric`
snapshot has this shape:

```json
{
  "schemaVersion": 1,
  "renderer": "code-metric",
  "title": "Code search",
  "generatedAtCall": 4,
  "xAxis": {
    "key": "primary_code",
    "label": "Code",
    "scale": "linear"
  },
  "metrics": [
    {"key": "metric_phase_0", "label": "Phase 0", "unit": null},
    {"key": "metric_phase_1", "label": "Phase 1", "unit": null}
  ],
  "rounds": [
    {"number": 1, "label": "Round 1", "decisionKind": "add_testcases"},
    {"number": 2, "label": "Round 2", "decisionKind": "add_testcases"},
    {"number": 3, "label": "Round 3", "decisionKind": "advance_stage"}
  ],
  "points": [
    {
      "id": "testcase-id-1",
      "testcaseId": "testcase-id-1",
      "testcaseName": "tc_001",
      "round": 1,
      "sequence": 1,
      "x": 8,
      "values": {
        "metric_phase_0": 0.31,
        "metric_phase_1": 0.34
      },
      "status": "succeeded"
    },
    {
      "id": "testcase-id-2",
      "testcaseId": "testcase-id-2",
      "testcaseName": "tc_002",
      "round": 2,
      "sequence": 2,
      "x": 9,
      "values": {
        "metric_phase_0": 0.25,
        "metric_phase_1": 0.27
      },
      "status": "succeeded"
    }
  ],
  "highlights": [
    {
      "pointId": "testcase-id-2",
      "metricKeys": [],
      "kind": "selected",
      "marker": "star",
      "prominence": "strong",
      "label": "Selected Code"
    }
  ]
}
```

`metrics` MAY contain four Phase series but the generic contract does not assume
four, and does not assume that the metric is named EyeLoss. The table MUST use
the same `points` and `metrics` as the line chart so the two views cannot drift.

## 5. Round semantics

Within one active Stage execution, `round` is the `algorithm_calls.call_number`
that generated the Testcase. It is not the Attempt number and is not the later
call that consumed the Testcase result.

Stage scope MUST include points generated by every Round in the selected active
Stage execution. Round scope MUST filter the same immutable point collection by
exact Round number. Switching scope MUST NOT ask the algorithm to recompute.

Superseded Stage executions are history and MUST NOT be merged into the active
Stage-wide plot. If history is exposed, the user first selects the Stage
execution and then selects `All rounds` or a Round within that execution.

The following rules preserve auditability:

- Every point MUST carry a stable `id`, `testcaseId`, `round`, and `sequence`.
- Repeated Code values from different Rounds MUST remain separate points.
- Stage scope SHOULD distinguish Rounds in Tooltip and MAY use point opacity or
  marker outline; Phase color remains the primary series encoding.
- Numeric Code uses `scale: linear`; opaque/string Code uses `scale: category`
  and `sequence` order.
- Missing metrics are `null`, render as gaps, and MUST NOT be converted to zero
  or connected across silently.
- An algorithm may highlight a point from any earlier Round in the active Stage.
- The latest persisted presentation snapshot is the default, while the API MAY
  expose earlier call snapshots for audit/debug use.

## 6. Highlights

Supported v1 values are deliberately bounded:

- `kind`: `selected`, `candidate`, `baseline`, `boundary`, or `warning`;
- `marker`: `circle`, `star`, `diamond`, or `triangle`;
- `prominence`: `normal` or `strong`;
- `metricKeys`: empty means every metric at the point; otherwise only listed
  metrics are emphasized.

The algorithm owns the semantic choice. The frontend maps it to accessible
visuals. `selected + star + strong` normally renders as a large star with a
strong outline and a Tooltip/legend explanation. Highlighting never changes the
underlying numeric value.

## 7. API and persistence

The compact `/api/v1/ui/snapshot` MUST NOT contain full presentation series.
Use a detail endpoint:

```text
GET /api/v1/run-corners/{runCornerId}/results
```

It returns dynamic Stage definitions, active/historical Stage executions,
algorithm-call metadata, and persisted presentation snapshots. The frontend
performs Stage/Round filtering locally from one selected snapshot.

Persist at least the presentation schema version, renderer key, payload JSON,
algorithm package name/version, and creation time with `algorithm_calls`. Do not
regenerate historical presentation with a newer package version.

## 8. Validation and fallback

The Backend MUST reject duplicate point IDs, unknown metric references,
non-finite numbers, invalid Round numbers, highlights that reference unknown
points, unsupported schema versions, labels beyond configured limits, excessive
point/metric counts, and oversized JSON. It MUST reject executable content and
raw chart-library options.

The frontend `code-metric@1` renderer MUST provide:

- `All rounds` and Round selection according to `roundDisplay`;
- Line/Table selection according to `availableViews`;
- metric-series visibility controls;
- Tooltip containing Code, metric values, Testcase, Round, status, and highlight
  meaning;
- a complete table containing Code, Round, Testcase, every metric, status, and
  highlight label;
- a safe table/unsupported fallback when renderer negotiation fails.

## 9. Required conformance cases

An algorithm package is conformant only when sanitized tests cover:

1. one Round and multiple Rounds;
2. Stage-wide aggregation and exact-Round filtering;
3. a progressively extended Code set;
4. repeated Code values in different Rounds;
5. four metric/Phase series and a non-four-series example;
6. selected star, strong point, boundary, and series-specific highlight;
7. missing/failed metric values;
8. numeric and categorical Code axes;
9. real physical names mapped to unrelated logical keys and labels;
10. unknown renderer/schema fallback and payload size limits.

## 10. Current implementation audit

The present code supports name replacement only partially:

| Area | Current support | Remaining work |
| --- | --- | --- |
| MT physical column names | Supported through `hspice.initialFields` and `hspice.resultFields`; mapping keys are returned as logical JSON keys | Add private fixtures for the real columns/format |
| PVT SP parameter names | Supported through `hspice.pvtParameterNames` | Remove UI assumptions about fixed PVT names if they differ |
| Algorithm-generated SP parameters | The algorithm may return arbitrary complete parameter mappings; the reference example also supports `parameterNames` | Real private package must perform the intended logical-to-physical mapping |
| Algorithm input/result persistence | Generic JSON dictionaries are preserved | Add schema/size validation for presentation payloads |
| Stage keys in Backend | Supplied dynamically by the algorithm | Frontend still imports fixed `STAGES` from mock data |
| Initial Code in Backend | Stored as an arbitrary JSON dictionary | Frontend normalization and detail views still hard-code `rangesel`, `vrefsel`, and `legsel` |
| Result metrics in Parser/algorithm input | Mapping keys are configurable | Reference algorithm assumes keys beginning with `loss_`; summary API assumes `loss` or `totalLoss` |
| Result charts/tables | Fixed examples only | Implement this contract and remove fixed four-Phase/Loss fields |
| Display names and units | Not provided dynamically | Read them from Stage/presentation descriptors |

Therefore changing only `site.json` is sufficient for the generic Parser and
renderer inputs to locate many real SP/MT fields, but it is not sufficient for
the current application to present and summarize those fields correctly. Full
support requires eliminating the reference-alias assumptions identified above.
