# FlowPilot GUI

## Real-machine integration

The UI and Electron supervisor are connected to the local Backend. Demo data is
still used for isolated UI scenarios and for algorithm-specific result charts
whose production presentation contract has not yet been implemented.

For the first LSF/HSPICE machine bring-up, start with
[`backend/docs/ON_MACHINE_AGENT_GUIDE.md`](backend/docs/ON_MACHINE_AGENT_GUIDE.md).
The detailed boundaries and acceptance sequence are in
[`backend/docs/INTEGRATION_CONTRACTS.md`](backend/docs/INTEGRATION_CONTRACTS.md)
and [`backend/docs/REAL_MACHINE_CHECKLIST.md`](backend/docs/REAL_MACHINE_CHECKLIST.md).
Algorithm packages and Results renderers follow
[`backend/docs/ALGORITHM_PRESENTATION_CONTRACT.md`](backend/docs/ALGORITHM_PRESENTATION_CONTRACT.md).

## Frontend Demo mode

Run the Electron application against an isolated, in-memory Demo Backend:

```bash
npm run demo
```

The Demo Backend uses port `8766`, never reads or writes the production database,
and resets to the same deterministic data whenever it starts. The main frontend
mutations and server-sent update events are supported.

Additional UI scenarios:

```bash
npm run demo:failures
npm run demo:large
npm run demo:empty
```

- `demo:failures` emphasizes recovery and error states.
- `demo:large` returns 5,000 Corners for performance and layout checks.
- `demo:empty` returns no datasets or runs for empty-state work.
