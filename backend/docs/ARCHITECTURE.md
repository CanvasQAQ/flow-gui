# Backend architecture

## Runtime ownership

The Electron main process starts one local Backend. The Backend owns SQLite,
workflow decisions, files, LSF submission, status reconciliation, postprocess,
and recovery. React only calls HTTP APIs.

```text
React / Electron
      |
      v
FastAPI on 127.0.0.1
      |
      +-- SQLite: durable business state, evidence, history, idempotency
      +-- immutable input snapshots
      +-- per-Run / Corner / Stage / Testcase directories
      +-- durable work queue
      |
      +-- Algorithm Adapter
      +-- HSPICE Renderer + Measure Parser
      +-- LSF Submit Adapter + public Parquet Snapshot Adapter
      +-- Postprocess Adapter
```

## Durable step model

The coordinator never waits inside one long workflow function. It performs one
of these recorded steps and returns:

1. initialize a Run Corner and its first Stage execution;
2. call an algorithm and persist its complete input/output;
3. render Testcase SP files and write a versioned immutable Batch manifest;
4. persist `ready`/`submitting` before calling `bsub`;
5. reconcile Status files and the latest public LSF snapshot;
6. enqueue one batch Postprocess when an algorithm-generated group is resolved;
7. persist normalized results and call the algorithm again;
8. add Testcases, advance to an algorithm-selected Stage, complete, or block.

`work_items.unique_key` prevents the same logical step from being queued twice.
API mutations use `Idempotency-Key`. SQLite WAL and `BEGIN IMMEDIATE` serialize
the small state-changing transactions.

## Important safety rules in code

- HSPICE rendering only modifies exact assignments inside `.PARAM` blocks.
- Missing or duplicate required parameter names fail before submission.
- Batch manifests are JSON, contiguous and one-based, and cannot be overwritten.
- Commands are argument arrays and never pass through `shell=True` or `eval`.
- Each status document carries Batch, Testcase, Attempt and Array Index identity.
- A matching `status=complete` plus expected outputs is the only automatic
  simulation-success path.
- LSF `DONE` without matching Status is `status_unknown`, not success.
- A Backend crash while a Batch is `submitting` becomes `status_unknown`; it is
  never blindly submitted again.
- Pause stops algorithm/submit steps but does not stop status collection or
  already-eligible Postprocess work.
- Historical rows and files are superseded, not deleted.

## Reference policy for historical Case resubmission

Resubmitting a Testcase in the active Stage reopens that Run Corner and runs a
new Postprocess generation followed by a new algorithm call. Resubmitting a
Testcase in an inactive historical Stage records the new execution and result,
but does not change the current path. Use the debug Rollback command to make
that historical Stage active before re-deciding.

