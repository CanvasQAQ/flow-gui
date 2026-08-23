export const CORNER_STATES = {
  NOT_STARTED: 'not_started',
  PREPARING: 'preparing',
  QUEUED: 'queued',
  RUNNING: 'running',
  COPYBACK_WAITING: 'copyback_waiting',
  POSTPROCESS: 'postprocess',
  WAITING_USER: 'waiting_user',
  STATUS_UNKNOWN: 'status_unknown',
  FAILED: 'failed',
  DONE: 'done',
  IGNORED: 'ignored',
};

export const STATE_META = {
  not_started: { label: 'Not started', color: '#94a3b8', tone: 'default' },
  preparing: { label: 'Preparing', color: '#8b5cf6', tone: 'purple' },
  queued: { label: 'LSF queued', color: '#d97706', tone: 'gold' },
  running: { label: 'Simulating', color: '#2563eb', tone: 'processing' },
  copyback_waiting: { label: 'Result copy failed', color: '#ea580c', tone: 'orange' },
  postprocess: { label: 'Postprocessing', color: '#0891b2', tone: 'cyan' },
  waiting_user: { label: 'Needs review', color: '#ea580c', tone: 'orange' },
  status_unknown: { label: 'Status unknown', color: '#c2410c', tone: 'volcano' },
  failed: { label: 'Failed', color: '#dc2626', tone: 'error' },
  done: { label: 'Complete', color: '#16a34a', tone: 'success' },
  ignored: { label: 'Ignored', color: '#64748b', tone: 'default' },
};

export const STAGES = [
  { key: 'stage1', name: 'Stage 1', description: 'Initial search' },
  { key: 'stage2', name: 'Stage 2', description: 'Local optimization' },
  { key: 'stage3', name: 'Final', description: 'Final validation' },
];

function seeded(index, factor = 37) {
  return ((index * factor + 17) % 997) / 997;
}

function pickState(index) {
  const n = index % 100;
  if (n < 58) return CORNER_STATES.DONE;
  if (n < 67) return CORNER_STATES.RUNNING;
  if (n < 73) return CORNER_STATES.QUEUED;
  if (n < 77) return CORNER_STATES.POSTPROCESS;
  if (n < 82) return CORNER_STATES.PREPARING;
  if (n < 87) return CORNER_STATES.NOT_STARTED;
  if (n < 92) return CORNER_STATES.FAILED;
  if (n < 96) return CORNER_STATES.STATUS_UNKNOWN;
  return CORNER_STATES.WAITING_USER;
}

function pickStage(index, state) {
  if (state === CORNER_STATES.NOT_STARTED || state === CORNER_STATES.PREPARING) return 'stage1';
  if (state === CORNER_STATES.DONE) return 'stage3';
  return index % 3 === 0 ? 'stage1' : index % 3 === 1 ? 'stage2' : 'stage3';
}

function buildCorners(count) {
  return Array.from({ length: count }, (_, index) => {
    const number = index + 1;
    const state = pickState(index);
    const stage = pickStage(index, state);
    const round = stage === 'stage1' ? (index % 3) + 1 : (index % 2) + 1;
    const testcaseTotal = state === CORNER_STATES.NOT_STARTED ? 0 : 5 + (index % 7) * 2;
    const testcaseDone = state === CORNER_STATES.DONE
      ? testcaseTotal
      : Math.min(testcaseTotal, Math.floor(seeded(index) * testcaseTotal));
    return {
      id: `corner_${String(number).padStart(4, '0')}`,
      number,
      state,
      stage,
      round,
      testcaseTotal,
      testcaseDone,
      inputVersion: index % 23 === 0 ? 'v2' : 'v1',
      initialCode: {
        rangesel: (index * 3) % 32,
        vrefsel: Array.from({ length: 4 }, (_, phase) => (index + phase * 2) % 8),
        legsel: Array.from({ length: 4 }, (_, phase) => (index + phase) % 5),
      },
      updateAvailable: index % 79 === 0,
      loss: state === CORNER_STATES.DONE ? +(0.18 + seeded(index, 71) * 0.35).toFixed(4) : null,
      updatedAt: `08-22 ${String(16 - (index % 7)).padStart(2, '0')}:${String((index * 7) % 60).padStart(2, '0')}`,
      issue: state === CORNER_STATES.FAILED
        ? 'LSF ended, but no matching status_complete was found for this execution.'
        : state === CORNER_STATES.STATUS_UNKNOWN
          ? 'The Job is absent from the latest public snapshot. Wait for a newer snapshot or confirm it manually.'
          : state === CORNER_STATES.WAITING_USER
            ? 'The current algorithm-call group contains unresolved failed Testcases.'
            : null,
    };
  });
}

export const DATASETS = [
  {
    id: 'dataset-01',
    name: 'adc_pvt_baseline_2026Q3',
    path: '/project/adc/calibration/baseline',
    scannedAt: '2026-08-22 15:42',
    total: 1312,
    available: 1284,
    invalid: 21,
    missing: 7,
    version: 4,
  },
];

export const INITIAL_RUNS = [
  {
    id: 'run-20260822-01',
    name: 'VT drift · nominal batch',
    datasetId: 'dataset-01',
    datasetName: 'adc_pvt_baseline_2026Q3',
    status: 'running',
    vdd: '0.72 V',
    vcm: '0.36 V',
    algorithm: 'Adaptive Search A',
    createdAt: '2026-08-22 09:18',
    corners: buildCorners(1248),
  },
  {
    id: 'run-20260821-02',
    name: 'VT drift · low VDD comparison',
    datasetId: 'dataset-01',
    datasetName: 'adc_pvt_baseline_2026Q3',
    status: 'paused',
    vdd: '0.68 V',
    vcm: '0.34 V',
    algorithm: 'Adaptive Search B',
    createdAt: '2026-08-21 13:05',
    corners: buildCorners(384).map((corner, index) => ({
      ...corner,
      state: index < 330 ? CORNER_STATES.DONE : corner.state,
    })),
  },
];

export function summarizeCorners(corners) {
  const summary = { total: corners.length };
  Object.values(CORNER_STATES).forEach((state) => { summary[state] = 0; });
  corners.forEach((corner) => { summary[corner.state] = (summary[corner.state] || 0) + 1; });
  summary.active = summary.preparing + summary.queued + summary.running + summary.postprocess;
  summary.attention = summary.waiting_user + summary.status_unknown + summary.failed + summary.copyback_waiting;
  return summary;
}

export function buildCases(runOrCorners) {
  if (!Array.isArray(runOrCorners) && Array.isArray(runOrCorners?.recoveryCases)) {
    return runOrCorners.recoveryCases.map((item) => ({
      ...item,
      key: item.testcaseId || item.id,
      testcaseId: item.testcaseId || item.id,
      testcase: item.testcase || item.name,
      cornerId: item.cornerId || `corner_${String(item.cornerNumber).padStart(4, '0')}`,
      stage: item.stage || item.stageKey,
      algorithmCall: item.algorithmCall || item.callNumber,
      state: item.state || item.status,
      lsfJob: item.lsfJob || item.lsfJobId || '—',
      attempt: item.attempt || item.attemptNumber || 1,
      attemptId: item.attemptId,
      evidence: item.copybackError?.message || item.evidence?.message || (typeof item.evidence === 'string' ? item.evidence : null) || item.error || 'Awaiting recovery decision',
      updatedAt: item.updatedAt || '—',
    }));
  }
  const corners = Array.isArray(runOrCorners) ? runOrCorners : runOrCorners?.corners || [];
  return corners
    .filter((corner) => [CORNER_STATES.FAILED, CORNER_STATES.STATUS_UNKNOWN, CORNER_STATES.WAITING_USER, CORNER_STATES.COPYBACK_WAITING].includes(corner.state))
    .flatMap((corner, cornerIndex) => Array.from({ length: cornerIndex % 3 === 0 ? 2 : 1 }, (_, caseIndex) => ({
      key: `${corner.id}-tc-${caseIndex + 1}`,
      testcaseId: null,
      testcase: `tc_${String(caseIndex + 1 + (corner.number % 9)).padStart(3, '0')}`,
      cornerId: corner.id,
      cornerNumber: corner.number,
      stage: corner.stage,
      algorithmCall: corner.round,
      state: corner.state === CORNER_STATES.WAITING_USER ? CORNER_STATES.FAILED : corner.state,
      lsfJob: corner.state === CORNER_STATES.STATUS_UNKNOWN ? '—' : `${812400 + corner.number}[${caseIndex + 1}]`,
      attempt: caseIndex + 1,
      evidence: corner.state === CORNER_STATES.FAILED ? 'LSF EXIT · incomplete status.json' : 'Not observed in the public snapshot',
      updatedAt: corner.updatedAt,
    })));
}
