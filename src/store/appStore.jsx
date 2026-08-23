import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useReducer, useRef } from 'react';
import { DATASETS, INITIAL_RUNS, summarizeCorners } from '../data/mockData';
import { getRuntimeStatus, getSnapshot, mutations, subscribeEvents } from '../api/backendClient';

const AppContext = createContext(null);

const ANIMATION_SETTING_KEY = 'flowpilot.animations-enabled';

function readAnimationsEnabled() {
  try { return window.localStorage.getItem(ANIMATION_SETTING_KEY) === 'true'; }
  catch { return false; }
}

const initialAnimationsEnabled = readAnimationsEnabled();
document.documentElement.classList.toggle('motion-disabled', !initialAnimationsEnabled);

const initialState = {
  datasets: DATASETS,
  runs: INITIAL_RUNS,
  activeRunId: INITIAL_RUNS[0].id,
  activeSection: 'runs',
  dataMode: 'loading',
  syncState: 'connecting',
  backend: { state: 'starting', message: 'Connecting to Backend' },
  checker: { state: 'unknown', message: 'Status check state unavailable' },
  revision: null,
  lastSyncedAt: null,
  error: null,
  animationsEnabled: initialAnimationsEnabled,
};

function normalizeDataset(dataset) {
  return {
    ...dataset,
    version: dataset.version ?? dataset.currentVersion ?? 1,
    total: dataset.total ?? 0,
    available: dataset.available ?? 0,
    invalid: dataset.invalid ?? 0,
    missing: dataset.missing ?? 0,
    scannedAt: dataset.scannedAt || 'Not scanned',
  };
}

function normalizeCorner(corner) {
  const status = corner.state || corner.status || 'not_started';
  const finalResult = corner.finalResult || {};
  const initialCode = corner.initialCode || {};
  return {
    ...corner,
    state: status,
    stage: corner.stage || corner.stageKey || corner.activeStage?.key || 'stage1',
    round: corner.round ?? corner.algorithmCall ?? corner.activeStage?.round ?? 1,
    testcaseTotal: corner.testcaseTotal ?? corner.progress?.total ?? 0,
    testcaseDone: corner.testcaseDone ?? corner.progress?.complete ?? 0,
    inputVersion: corner.inputVersion || corner.inputVersionId || 'Pinned',
    initialCode: { rangesel: initialCode.rangesel ?? '—', vrefsel: initialCode.vrefsel || ['—', '—', '—', '—'], legsel: initialCode.legsel || ['—', '—', '—', '—'] },
    updateAvailable: corner.updateAvailable ?? corner.inputUpdateAvailable ?? false,
    loss: corner.loss ?? finalResult.loss ?? finalResult.totalLoss ?? null,
    updatedAt: corner.updatedAt || '—',
    workflowSteps: corner.workflowSteps || corner.activeStage?.steps || corner.substeps || null,
  };
}

function normalizeRun(run) {
  const pvt = run.pvt || {};
  return {
    ...run,
    datasetId: run.datasetId || run.dataset?.id,
    datasetName: run.datasetName || run.dataset?.name || 'Unknown input',
    status: run.status || 'running',
    vdd: run.vdd || pvt.vdd || pvt.VDD || '—',
    vcm: run.vcm || pvt.vcm || pvt.VCM || '—',
    corners: (run.corners || []).map(normalizeCorner),
    recoveryCases: run.recoveryCases || [],
  };
}

function reducer(state, action) {
  switch (action.type) {
    case 'SET_SECTION':
      return { ...state, activeSection: action.value };
    case 'SET_ACTIVE_RUN':
      return { ...state, activeRunId: action.id, activeSection: 'runs' };
    case 'SET_RUN_STATE':
      return { ...state, runs: state.runs.map((run) => run.id === state.activeRunId ? { ...run, status: action.value } : run) };
    case 'SET_ANIMATIONS_ENABLED':
      return { ...state, animationsEnabled: action.value };
    case 'SNAPSHOT_RECEIVED': {
      const datasets = (action.payload.datasets || []).map(normalizeDataset);
      const runs = (action.payload.runs || []).map(normalizeRun);
      const activeRunId = runs.some((run) => run.id === state.activeRunId) ? state.activeRunId : runs[0]?.id || null;
      return {
        ...state, datasets, runs, activeRunId, dataMode: 'live', error: null,
        revision: action.payload.revision ?? state.revision, lastSyncedAt: new Date().toISOString(),
      };
    }
    case 'SNAPSHOT_FAILED':
      return { ...state, dataMode: state.dataMode === 'live' ? 'stale' : 'demo', error: action.error };
    case 'SYNC_STATE':
      return { ...state, syncState: action.value };
    case 'RUNTIME_RECEIVED':
      return {
        ...state,
        backend: action.payload.backend && typeof action.payload.backend === 'object'
          ? action.payload.backend : { state: action.payload.backend || action.payload.state || 'ready' },
        checker: action.payload.checker || action.payload.statusChecker || state.checker,
      };
    case 'ELECTRON_STATUS':
      return { ...state, backend: { ...state.backend, ...action.payload } };
    default:
      return state;
  }
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const refreshInFlight = useRef(null);

  useLayoutEffect(() => {
    document.documentElement.classList.toggle('motion-disabled', !state.animationsEnabled);
    try { window.localStorage.setItem(ANIMATION_SETTING_KEY, String(state.animationsEnabled)); }
    catch { /* Keep the in-memory setting when storage is unavailable. */ }
  }, [state.animationsEnabled]);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    refreshInFlight.current = getSnapshot()
      .then((payload) => { dispatch({ type: 'SNAPSHOT_RECEIVED', payload }); return payload; })
      .catch((error) => { dispatch({ type: 'SNAPSHOT_FAILED', error: error.message }); throw error; })
      .finally(() => { refreshInFlight.current = null; });
    return refreshInFlight.current;
  }, []);

  const refreshRuntime = useCallback(async () => {
    try { dispatch({ type: 'RUNTIME_RECEIVED', payload: await getRuntimeStatus() }); }
    catch (error) { dispatch({ type: 'ELECTRON_STATUS', payload: { state: 'degraded', message: error.message } }); }
  }, []);

  useEffect(() => {
    let disposed = false;
    let refreshTimer;
    let runtimeTimer;
    let eventDebounce;
    refresh().catch(() => {});
    refreshRuntime();
    const unsubscribeEvents = subscribeEvents({
      onConnectionChange(value) {
        if (!disposed) dispatch({ type: 'SYNC_STATE', value });
      },
      onEvent() {
        clearTimeout(eventDebounce);
        eventDebounce = setTimeout(() => refresh().catch(() => {}), 150);
      },
    });
    refreshTimer = setInterval(() => refresh().catch(() => {}), 15_000);
    runtimeTimer = setInterval(refreshRuntime, 10_000);

    const electron = window.flowGui;
    electron?.backend?.getStatus?.().then((payload) => !disposed && dispatch({ type: 'ELECTRON_STATUS', payload }));
    const unsubscribeBackend = electron?.backend?.onStatus?.((payload) => dispatch({ type: 'ELECTRON_STATUS', payload }));
    return () => {
      disposed = true;
      unsubscribeEvents();
      unsubscribeBackend?.();
      clearInterval(refreshTimer);
      clearInterval(runtimeTimer);
      clearTimeout(eventDebounce);
    };
  }, [refresh, refreshRuntime]);

  const actions = useMemo(() => ({
    async refresh() { return refresh(); },
    async createDataset(input) { const result = await mutations.createDataset(input); await refresh(); return result; },
    async scanDataset(datasetId, mode) { const result = await mutations.scanDataset(datasetId, mode); await refresh(); return result; },
    async setRunPaused(runId, paused) { const result = await mutations.setRunPaused(runId, paused); await refresh(); return result; },
    async createRun(input) { const result = await mutations.createRun(input); await refresh(); return result; },
    async addCorners(runId, cornerNumbers) { const result = await mutations.addCorners(runId, cornerNumbers); await refresh(); return result; },
    async previewRecovery(runId, action, filters) { return mutations.previewRecovery(runId, action, filters); },
    async confirmRecovery(token) { const result = await mutations.confirmRecovery(token); await refresh(); return result; },
    async retryCopyback(attemptId) { const result = await mutations.retryCopyback(attemptId); await refresh(); return result; },
    backend: {
      start: () => window.flowGui?.backend?.start?.(),
      stop: () => window.flowGui?.backend?.stop?.(),
      restart: () => window.flowGui?.backend?.restart?.(),
      openLogs: () => window.flowGui?.backend?.openLogs?.(),
    },
  }), [refresh]);
  const value = useMemo(() => ({ state, dispatch, actions }), [state, actions]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}

export function selectActiveRun(state) {
  return state.runs.find((run) => run.id === state.activeRunId) || state.runs[0] || { id: null, name: 'No Runs', status: 'paused', corners: [], vdd: '—', vcm: '—', algorithm: '—' };
}

export { summarizeCorners };
