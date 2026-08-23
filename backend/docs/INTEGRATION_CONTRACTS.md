# Site integration contracts

This document is the implementation boundary for the real LSF/HSPICE machine.
Project netlists and results do not need to leave that machine.

## 1. Dataset discovery

The default convention matches relative files with:

```text
^(?P<stem>.+)\.sp(?P<corner>[^/]*)$
```

and finds the MT peer with `{stem}.mt{corner}`. Both are configurable in
`site.json`; recursive scanning is optional. Examples:

```text
base.sp1  <-> base.mt1
base.sp_tt_002 <-> base.mt_tt_002
```

Requirements:

- `corner` must be stable across scans;
- if its suffix ends in digits, those digits become the displayed Corner number;
- otherwise the sorted discovery order supplies the number;
- duplicate numbers are assigned the next free number but should be avoided;
- append scanning does not reread an already-valid Corner;
- overwrite scanning fingerprints SP + MT and creates a new immutable version
  only when content changes;
- parsing failure affects only that Corner.

## 2. HSPICE SP rendering

Synopsys documents `.PARAM ParamName=RealNumber | expression`. The reference
renderer supports scalar assignments such as:

```spice
.param rangeselcode = 12 vdd=0.72
+ vrefsel0code='3'
```

Requirements for the production templates:

- each required Code/PVT parameter must be defined exactly once;
- names are matched case-insensitively and exactly;
- comments and similarly prefixed names are not modified;
- every Testcase is rendered from the immutable original SP snapshot;
- algorithms return a complete Code/Mode mapping; Workflow never merges Code;
- PVT logical names are mapped through `hspice.pvtParameterNames`;
- expressions may remain elsewhere in the file, but replacement values supplied
  by the Workflow must be valid HSPICE scalar tokens.

If real templates intentionally override the same `.PARAM` more than once, do
not disable duplicate checking casually. Implement a site renderer that defines
which occurrence is authoritative and add a confidential-machine conformance
test.

## 3. HSPICE MT parsing

The reference parser targets traditional whitespace-delimited HSPICE measure
data and `MEASFORM=1`. It supports wrapped column/value lines, scientific
notation, HSPICE engineering suffixes, and failure tokens such as `failed`.

The first non-metadata tokens are column names; remaining tokens form one or
more rows. Initialization currently requires exactly one row. Configure logical
fields under:

```json
{
  "hspice": {
    "initialFields": {"rangesel": "rangeselcode"},
    "resultFields": {"loss_0": "loss_0"}
  }
}
```

On the real machine confirm:

- whether output is traditional, `MEASFORM=1`, or CSV `MEASFORM=3`;
- exact case-insensitive column names;
- whether failed measures appear as `failed`, zero, or another sentinel;
- whether one file can contain several ALTER/Monte Carlo rows;
- whether the initialization MT contains both the nine Code values and four
  reference Loss values.

CSV `MEASFORM=3` must use a separate CSV adapter; it must not be silently fed to
the whitespace parser.

## 4. Algorithm package

Each scheme is configured as a Python factory:

```json
{
  "algorithms": {
    "adaptive-a": {
      "factory": "company_flow.algorithms.adaptive_a:create_algorithm",
      "config": {}
    }
  }
}
```

Install the company package into the Backend virtual environment. The factory
receives the JSON config and returns an object with:

```python
def stage_definitions() -> list[dict]: ...
def decide(stage_key: str, payload: dict) -> dict: ...
```

Stage keys and Testcase names must be unique and filesystem-safe
`[A-Za-z0-9._-]+`. A decision is exactly one of:

```json
{"kind":"add_testcases","reason":"...","testcases":[{"name":"tc_001","parameters":{"rangeselcode":12}}]}
{"kind":"advance_stage","reason":"...","result":{},"nextStage":"stage_2"}
{"kind":"complete","reason":"...","result":{}}
{"kind":"cannot_continue","reason":"..."}
```

Every proposed Testcase must contain the complete Code/Mode parameter mapping
needed to render a fresh SP. See
`flow_backend.examples.reference_algorithm:create_algorithm` for a synthetic
two-Stage contract example; it is not a production search algorithm.

### Algorithm-owned result presentation

The product plan requires the Results tab to vary by algorithm scheme and
Stage. That boundary is **not implemented yet**. The current Python algorithm
contract carries only Stage definitions and workflow decisions. It has no
presentation method or schema; the UI snapshot does not contain Stage testcase
results; and the React renderers currently use hard-coded scheme names and
synthetic chart data.

Do not put confidential algorithm logic into React and do not dynamically load
Python-package code in the renderer. Before a production algorithm-specific
chart is enabled, implement a versioned, declarative presentation contract:

1. extend each Stage definition with a stable `presentation.renderer` key and
   `presentation.schemaVersion`;
2. persist a JSON-safe presentation payload produced from the algorithm's
   already-recorded input/result, without executable JavaScript or raw ECharts
   options;
3. expose Stage definitions and per-Corner Stage result payloads through the
   local API (prefer a detail endpoint rather than enlarging `/ui/snapshot`);
4. register an allow-listed React renderer for each supported renderer key and
   provide an accessible table fallback;
5. validate payload size, required fields, numeric finiteness, labels, and
   schema version in the Backend;
6. add a contract fixture and Backend/Frontend tests before replacing the
   current synthetic charts.

The intended ownership boundary is: the site algorithm package owns the
meaning and JSON data of a visualization; the packaged frontend owns trusted
rendering components and visual styling. A new visualization shape therefore
requires both an algorithm-package payload producer and a reviewed frontend
renderer. See `ON_MACHINE_AGENT_GUIDE.md` for the exact current files and a
proposed implementation path. The normative payload, multi-Round aggregation,
highlighting, naming, validation, and fallback rules are defined in
`ALGORITHM_PRESENTATION_CONTRACT.md`.

## 5. Batch and compute-node Runner

One Corner + Stage execution + algorithm call forms one Batch group. One item
uses a normal Job; several use an Array Job. The immutable JSON manifest records
one-based `index`, Testcase/Attempt identity and absolute fixed paths.

The submit adapter builds argument arrays equivalent to:

```text
bsub -J flow_x[1-N]%limit -q queue ... env FLOW_SCRATCH_ROOT=/SCRATCH flow-batch-runner manifest.json
```

The Runner reads `LSB_JOBINDEX` (or uses index 1), obtains its actual hostname,
and creates an Attempt-specific zero-byte hostname marker on the network disk.
It copies only the manifest SP into
`/SCRATCH/<user>/flowpilot/<attempt_id>`, runs the simulator there without a
shell, captures stdout/stderr, and copies the complete Scratch directory into
the Attempt return directory. Atomic `status.json` transitions preserve the
Attempt identity, Scratch/return paths, simulator exit code, copy-back error,
and wrapper exit code.

If stage-out fails after a successful simulation, Recovery uses ordinary SSH
to the validated hostname marker and retries only the complete-directory copy.
The project does not disable host-key checking and does not manage or infer the
site's known 24-hour Scratch cleanup. Verify `/SCRATCH`, `$USER`, hostname form,
known_hosts/authentication, remote `cp -a`, and disk-full behavior on the real
cluster.

The compute node must be able to execute `flow-batch-runner`. Either install the
wheel in the shared environment or replace the command with a deployed wrapper
that preserves the same manifest/status contract.

## 6. Public `bjobs.parquet`

All column names are configured. At minimum provide Job ID, Job name and state;
Array Index is required for Array-level reconciliation. Optional fields include
exit code, execution host and timestamps.

The reader identifies a snapshot by modification time and size, opens it through
DuckDB once, and filters all active FlowPilot Job names in one query. Confirm on
the real system:

- file publication is atomic;
- timestamp/time zone semantics;
- state spelling (`PEND`, `RUN`, `DONE`, `EXIT`, `UNKWN`, suspends);
- Array Index type;
- how long terminal rows remain;
- whether Job name is complete rather than display-truncated.

LSF Job names are not inherently unique. FlowPilot generates a unique Batch name
before `bsub` and persists it in SQLite and the manifest.

## 7. Postprocess

The configured command runs once per resolved algorithm-call group with the
Stage directory as its working directory. The command processes waveform files
and creates HSPICE-format MT files; it does not return or write JSON for
FlowPilot. After a zero exit code, the Backend locates each non-ignored
Testcase's MT using `postprocess.mtFilePattern`, parses it through the same
configured HSPICE result-field adapter, and only then builds its internal result
object for the algorithm.

The configured pattern should resolve to exactly one MT file per Case. A
nonzero command exit, a missing or ambiguous MT match, an unreadable MT, or a
missing required result field keeps the Corner out of algorithm advancement.
Stdout and stderr are saved in the Stage directory. On the real machine, prefer
an exact filename pattern over a broad wildcard when the Case directory can
contain historical MT files.

## 8. API rules

- Bind only to `127.0.0.1`.
- Every mutation supplies a stable `Idempotency-Key` header.
- Dataset rescan and Case resubmission use preview/confirm semantics where
  applicable.
- UI never reads simulation directories, SQLite, Parquet, or LSF directly.
- `/api/v1/integration-checks` must be green before enabling real submission.
- Keep `lsf.dryRun=true` until manifest, command, path and permissions have been
  reviewed on the real machine.

Current limitation: `integration-checks.ready` is not yet a hard submission
gate and does not currently mean that every returned item is `ok`. The
on-machine operator must inspect all items and keep `dryRun=true`; production
work should make submission reject non-dry-run operation until all required
checks and an explicit site approval are recorded.

## Reference documentation

- [Synopsys HSPICE Quick Reference](https://www.synopsys.com/content/dam/synopsys/verification/hspice-quickrefcard-M-2017-03.pdf)
- [IBM Spectrum LSF Job Arrays](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=administration-job-arrays)
- [IBM `bsub -J` option](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=o-j)
- [IBM `bjobs` fields and states](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=bjobs-description)
