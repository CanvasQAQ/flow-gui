import { createContext, useContext, useMemo, useReducer } from 'react';
import { INITIAL_VCMS, CORNER_STATES } from '../data/mockData';

const AppContext = createContext(null);

const initialState = {
  project: {
    root: '/projects/calibration',
    output: '/projects/calibration/output',
    mode: 'Debug',
  },
  vcms: INITIAL_VCMS,
  activeVcmId: INITIAL_VCMS[0].id,
  runState: 'idle',
};

function reducer(state, action) {
  switch (action.type) {
    case 'SET_ACTIVE_VCM':
      return { ...state, activeVcmId: action.id };
    case 'SET_RUN_STATE':
      return { ...state, runState: action.value };
    case 'SET_MODE':
      return { ...state, project: { ...state.project, mode: action.value } };
    case 'SET_PROJECT_ROOT':
      return { ...state, project: { ...state.project, root: action.value } };
    case 'SET_OUTPUT_DIR':
      return { ...state, project: { ...state.project, output: action.value } };
    case 'ADVANCE_CORNERS': {
      const vcms = state.vcms.map((vcm) => {
        if (vcm.id !== state.activeVcmId) return vcm;
        const corners = vcm.corners.map((c) => {
          if (c.state === CORNER_STATES.DONE || c.state === CORNER_STATES.WAITING_USER || c.state === CORNER_STATES.FAILED)
            return c;
          if (c.state === CORNER_STATES.NO_BASELINE) return c;
          const next = transition(c);
          return { ...c, ...next, updatedAt: Date.now() };
        });
        return { ...vcm, corners };
      });
      return { ...state, vcms };
    }
    default:
      return state;
  }
}

function transition(c) {
  if (c.state === CORNER_STATES.NOT_STARTED) {
    return { state: CORNER_STATES.RUNNING, stage: 'stage1', iteration: 1, lastAction: 'submitting job', nextStep: 'param scan' };
  }
  if (c.state === CORNER_STATES.RUNNING) {
    const order = ['stage1', 'stage2', 'stage3', 'stage4'];
    const idx = order.indexOf(c.stage);
    if (idx < order.length - 1) {
      const nextStage = order[idx + 1];
      if (nextStage === 'stage4' && Math.random() < 0.15) {
        return { state: CORNER_STATES.WAITING_USER, stage: nextStage, lastAction: 'awaiting user review', nextStep: 'manual review' };
      }
      return { state: CORNER_STATES.RUNNING, stage: nextStage, lastAction: `running ${nextStage}`, nextStep: `${nextStage} processing` };
    }
    return { state: CORNER_STATES.DONE, stage: 'stage4', iteration: c.iteration + 1, lastAction: 'validation done', nextStep: 'complete' };
  }
  return {};
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used inside AppProvider');
  return ctx;
}

export function selectActiveVcm(state) {
  return state.vcms.find((v) => v.id === state.activeVcmId) || state.vcms[0];
}

export function summarize(corners) {
  const acc = {
    total: corners.length,
    not_started: 0,
    no_baseline: 0,
    running: 0,
    waiting_user: 0,
    failed: 0,
    done: 0,
  };
  for (const c of corners) acc[c.state] = (acc[c.state] || 0) + 1;
  return acc;
}
