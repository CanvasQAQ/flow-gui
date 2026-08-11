export const CORNER_STATES = {
  NOT_STARTED: 'not_started',
  NO_BASELINE: 'no_baseline',
  RUNNING: 'running',
  WAITING_USER: 'waiting_user',
  FAILED: 'failed',
  DONE: 'done',
};

export const STATE_META = {
  [CORNER_STATES.NOT_STARTED]: { label: 'Not Started', color: '#e0e0e0', textColor: '#8c8c8c' },
  [CORNER_STATES.NO_BASELINE]: { label: 'No Baseline', color: '#d9d9d9', textColor: '#595959' },
  [CORNER_STATES.RUNNING]: { label: 'Running', color: '#1677ff', textColor: '#fff' },
  [CORNER_STATES.WAITING_USER]: { label: 'Waiting User', color: '#faad14', textColor: '#fff' },
  [CORNER_STATES.FAILED]: { label: 'Failed', color: '#ff4d4f', textColor: '#fff' },
  [CORNER_STATES.DONE]: { label: 'Done', color: '#52c41a', textColor: '#fff' },
};

export const STAGE_KEYS = {
  STAGE1: 'stage1',
  STAGE2: 'stage2',
  STAGE3: 'stage3',
  FINAL: 'stage4',
};

export const STAGE_LABELS = {
  [STAGE_KEYS.STAGE1]: 'Stage 1 · Parameter Scan',
  [STAGE_KEYS.STAGE2]: 'Stage 2 · Reference Sweep',
  [STAGE_KEYS.STAGE3]: 'Stage 3 · Iterative Tuning',
  [STAGE_KEYS.FINAL]: 'Stage 4 · Validation',
};

const PHASES = ['A', 'B', 'C', 'D'];
const SELS = Array.from({ length: 32 }, (_, i) => i);

function pseudoRand(seed) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function buildCandidate(seed, stage) {
  const r = pseudoRand(seed);
  const code_x = Math.floor(r() * 32);
  const ref_sel = PHASES.map(() => Math.floor(r() * 8));
  const code_y = PHASES.map(() => Math.floor(r() * 5));
  const metric = PHASES.map(() => +(r() * 0.6 + 0.2).toFixed(3));
  const metric_sum = +metric.reduce((a, b) => a + b, 0).toFixed(3);
  const metric_max = +Math.max(...metric).toFixed(3);
  const limitHit = stage === STAGE_KEYS.STAGE3 || stage === STAGE_KEYS.FINAL;
  return {
    code_x, ref_sel, code_y, metric, metric_sum, metric_max,
    limit_saturated: limitHit && r() > 0.7,
  };
}

function buildStage1(seed) {
  const r = pseudoRand(seed);
  const period = r() * Math.PI * 2;
  const trend = 0.55 + (seed % 100) / 200;
  let runningMin = Infinity;
  let stopIdx = null;
  const groups = [{ startIdx: 31, endIdx: 27, maxVal: -Infinity }];

  for (let idx = 31; idx >= 0; idx -= 1) {
    if (idx <= groups[groups.length - 1].endIdx - 1) {
      const prevMax = groups[groups.length - 1].maxVal;
      groups.push({ startIdx: idx, endIdx: Math.max(idx - 4, 0), maxVal: -Infinity });
      if (!stopIdx && idx < 31 && prevMax > 0) stopIdx = idx + 4;
    }
    const sa = PHASES.map((_, pi) => {
      const wave = Math.sin(idx * 0.22 + period + pi * 0.8) * 0.4;
      const drift = (31 - idx) * 0.012;
      return +(trend + wave + drift + r() * 0.25).toFixed(3);
    });
    const sum = +sa.reduce((a, b) => a + b, 0).toFixed(3);
    const max = +Math.max(...sa).toFixed(3);
    if (sum < runningMin) runningMin = sum;
    const g = groups[groups.length - 1];
    g.maxVal = Math.max(g.maxVal, max);
  }

  return { sels: SELS.map((idx) => {
    const sa = PHASES.map((_, pi) => {
      const wave = Math.sin(idx * 0.22 + period + pi * 0.8) * 0.4;
      const drift = (31 - idx) * 0.012;
      return +(trend + wave + drift + r() * 0.25).toFixed(3);
    });
    return { idx, sa, sum: +sa.reduce((a, b) => a + b, 0).toFixed(3) };
  }), stopIdx, bestIdx: SELS.reduce((best, idx) => {
    const sa = PHASES.map((_, pi) => {
      const wave = Math.sin(idx * 0.22 + period + pi * 0.8) * 0.4;
      const drift = (31 - idx) * 0.012;
      return +(trend + wave + drift + r() * 0.25).toFixed(3);
    });
    const sum = +sa.reduce((a, b) => a + b, 0).toFixed(3);
    return sum < best.sum ? { idx, sum } : best;
  }, { idx: 0, sum: Infinity }).idx };
}

function buildStage2(seed) {
  const r = pseudoRand(seed);
  const code_x = Math.floor(r() * 32);
  const CODES = Array.from({ length: 5 }, (_, i) => i);
  const items = CODES.map((code) => {
    const sa = PHASES.map((_, pi) => {
      const base = 0.3 + (code * 0.08) + (pi * 0.05);
      return +(base + r() * 0.35).toFixed(3);
    });
    const limitHit = code === 0 || code === 4;
    return { code, sa, metric_sum: +sa.reduce((a, b) => a + b, 0).toFixed(3), limit_hit: limitHit };
  });
  const best = items.reduce((a, b) => a.metric_sum < b.metric_sum ? a : b);
  return { code_x, items, best_code: best.code, best_metric: best.metric_sum };
}

function buildStage3(seed) {
  const r = pseudoRand(seed);
  const iterations = [];
  let active = 'A';
  for (let i = 0; i < 3; i += 1) {
    active = PHASES[Math.floor(r() * PHASES.length)];
    iterations.push({
      round: i + 1, active,
      best_code: Math.floor(r() * 5),
      best_metric: +(0.25 + r() * 0.4).toFixed(3),
      limit_hit: r() > 0.6,
      saturated: i === 2 && r() > 0.5,
    });
  }
  return iterations;
}

function buildFinal(seed) {
  const r = pseudoRand(seed);
  return {
    testcases: 96,
    phases: PHASES.map((p) => ({ phase: p, metric: +(0.2 + r() * 0.3).toFixed(3) })),
    metric_sum: +(0.8 + r() * 1.2).toFixed(3),
    metric_max: +(0.4 + r() * 0.3).toFixed(3),
    passed: r() > 0.2,
  };
}

function buildPlanner(state, stage) {
  if (state === CORNER_STATES.FAILED) {
    return { current_stage_complete: false, next_stage: null,
      reason: 'Job execution failed; queue worker returned no response.', };
  }
  if (state === CORNER_STATES.WAITING_USER) {
    return { current_stage_complete: false, next_stage: stage,
      reason: 'Post-process produced candidate with metric above threshold; manual review required.', };
  }
  if (state === CORNER_STATES.DONE) {
    return { current_stage_complete: true, next_stage: null,
      reason: 'Validation passed; result committed.', };
  }
  if (stage === STAGE_KEYS.STAGE1) {
    return { current_stage_complete: false, next_stage: STAGE_KEYS.STAGE2,
      reason: 'Stage 1 parameter scan in progress, evaluating 32 candidates.', };
  }
  if (stage === STAGE_KEYS.STAGE2) {
    return { current_stage_complete: true, next_stage: STAGE_KEYS.STAGE3,
      reason: 'Limit hit on active phase: advancing to Stage 3 iterative tuning.', };
  }
  if (stage === STAGE_KEYS.STAGE3) {
    return { current_stage_complete: true, next_stage: STAGE_KEYS.FINAL,
      reason: 'Stage 3 iteration converged; ready for validation.', };
  }
  return { current_stage_complete: true, next_stage: null,
    reason: 'Awaiting planner tick.', };
}

function pickState(idx, total) {
  const r = (idx * 37) % total;
  if (r % 23 === 0) return CORNER_STATES.FAILED;
  if (r % 19 === 0) return CORNER_STATES.WAITING_USER;
  if (r % 17 === 0) return CORNER_STATES.NO_BASELINE;
  if (r % 29 === 0) return CORNER_STATES.NOT_STARTED;
  if (r < total * 0.58) return CORNER_STATES.DONE;
  if (r < total * 0.78) return CORNER_STATES.RUNNING;
  return CORNER_STATES.NOT_STARTED;
}

function pickStage(state) {
  if (state === CORNER_STATES.DONE) return STAGE_KEYS.FINAL;
  if (state === CORNER_STATES.NOT_STARTED) return null;
  if (state === CORNER_STATES.NO_BASELINE) return null;
  if (state === CORNER_STATES.WAITING_USER) return STAGE_KEYS.STAGE2;
  if (state === CORNER_STATES.FAILED) return STAGE_KEYS.STAGE2;
  const r = Math.random();
  if (r < 0.25) return STAGE_KEYS.STAGE1;
  if (r < 0.55) return STAGE_KEYS.STAGE2;
  if (r < 0.8) return STAGE_KEYS.STAGE3;
  return STAGE_KEYS.FINAL;
}

export function buildVcm(id, name, cornerCount) {
  const corners = [];
  for (let i = 0; i < cornerCount; i += 1) {
    const state = pickState(i, cornerCount);
    const stage = pickStage(state);
    const seed = id * 100000 + i;
    const r = pseudoRand(seed);
    corners.push({
      id: `${id}-C${String(i + 1).padStart(5, '0')}`,
      vcmId: id,
      index: i,
      state, stage,
      iteration: state === CORNER_STATES.RUNNING || state === CORNER_STATES.WAITING_USER ? Math.floor(r() * 3) + 1 : 0,
      batchId: `B${String(Math.floor(i / 12) + 1).padStart(4, '0')}`,
      caseId: `case_${i + 1}`,
      lastAction: state === CORNER_STATES.RUNNING ? 'submitting job' : state === CORNER_STATES.DONE ? 'validation done' : 'idle',
      nextStep: stage === STAGE_KEYS.STAGE1 ? 'param scan' : stage === STAGE_KEYS.STAGE2 ? 'ref sweep' : stage === STAGE_KEYS.STAGE3 ? 'iterative tuning' : stage === STAGE_KEYS.FINAL ? 'validation' : 'await baseline',
      error: state === CORNER_STATES.FAILED ? 'Queue worker returned no response after 600s timeout.' : null,
      candidate: [STAGE_KEYS.STAGE2, STAGE_KEYS.STAGE3, STAGE_KEYS.FINAL].includes(stage) || state === CORNER_STATES.DONE ? buildCandidate(seed, stage) : null,
      planner: buildPlanner(state, stage),
      stage1: state !== CORNER_STATES.NOT_STARTED && state !== CORNER_STATES.NO_BASELINE ? buildStage1(seed) : null,
      stage2: [STAGE_KEYS.STAGE2, STAGE_KEYS.STAGE3, STAGE_KEYS.FINAL].includes(stage) || state === CORNER_STATES.DONE ? buildStage2(seed) : null,
      stage3: [STAGE_KEYS.STAGE3, STAGE_KEYS.FINAL].includes(stage) || state === CORNER_STATES.DONE ? buildStage3(seed) : null,
      stage4: state === CORNER_STATES.DONE ? buildFinal(seed) : null,
      updatedAt: Date.now() - Math.floor(r() * 3600 * 1000),
    });
  }
  return { id, name, corners };
}

export const INITIAL_VCMS = [
  buildVcm(1, 'VCM A', 2240),
  buildVcm(2, 'VCM B', 1480),
];
