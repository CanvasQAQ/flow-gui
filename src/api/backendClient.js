const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';

export const backendBaseUrl = (window.flowGui?.backend?.baseUrl || import.meta.env.VITE_BACKEND_URL || DEFAULT_BASE_URL).replace(/\/$/, '');

export class BackendError extends Error {
  constructor(message, status = 0, detail = null) {
    super(message);
    this.name = 'BackendError';
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${backendBaseUrl}${path}`, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || response.statusText;
    throw new BackendError(String(detail || 'Backend request failed'), response.status, payload);
  }
  return payload;
}

function idempotencyKey(scope) {
  return `${scope}-${crypto.randomUUID()}`;
}

async function legacySnapshot() {
  const [datasetsPayload, runsPayload] = await Promise.all([
    request('/api/v1/datasets'),
    request('/api/v1/runs'),
  ]);
  const runs = await Promise.all((runsPayload?.items || []).map(async (run) => {
    const corners = await request(`/api/v1/runs/${encodeURIComponent(run.id)}/corners`)
      .then((payload) => payload?.items || [])
      .catch(() => []);
    return { ...run, corners };
  }));
  return { revision: null, datasets: datasetsPayload?.items || [], runs };
}

export async function getSnapshot() {
  try {
    return await request('/api/v1/ui/snapshot');
  } catch (error) {
    if (error instanceof BackendError && error.status === 404) return legacySnapshot();
    throw error;
  }
}

export async function getRuntimeStatus() {
  try {
    return await request('/api/v1/runtime/status');
  } catch (error) {
    if (error instanceof BackendError && error.status === 404) {
      const health = await request('/api/v1/health');
      return { backend: health.status === 'ok' ? 'ready' : 'degraded', checker: { state: 'unknown' } };
    }
    throw error;
  }
}

export function subscribeEvents({ onEvent, onConnectionChange }) {
  const source = new EventSource(`${backendBaseUrl}/api/v1/events`);
  source.onopen = () => onConnectionChange?.('connected');
  const handleEvent = (event) => {
    let payload = null;
    try { payload = JSON.parse(event.data); } catch { payload = { type: 'changed' }; }
    onEvent?.({ ...payload, eventId: payload?.eventId || event.lastEventId || null });
  };
  source.onmessage = handleEvent;
  source.addEventListener('state', handleEvent);
  source.onerror = () => onConnectionChange?.('disconnected');
  return () => source.close();
}

export const mutations = {
  createDataset(input) {
    return request('/api/v1/datasets', {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('dataset') }, body: JSON.stringify(input),
    });
  },
  scanDataset(datasetId, mode = 'append') {
    return request(`/api/v1/datasets/${encodeURIComponent(datasetId)}/scan`, {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('scan') }, body: JSON.stringify({ mode }),
    });
  },
  setRunPaused(runId, paused) {
    return request(`/api/v1/runs/${encodeURIComponent(runId)}/pause`, {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('pause') }, body: JSON.stringify({ paused }),
    });
  },
  createRun(input) {
    return request('/api/v1/runs', {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('run') }, body: JSON.stringify(input),
    });
  },
  addCorners(runId, cornerNumbers) {
    return request(`/api/v1/runs/${encodeURIComponent(runId)}/corners`, {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('corners') }, body: JSON.stringify({ cornerNumbers }),
    });
  },
  previewRecovery(runId, action, filters) {
    return request(`/api/v1/runs/${encodeURIComponent(runId)}/case-recovery/preview`, {
      method: 'POST', body: JSON.stringify({ action, filters }),
    });
  },
  confirmRecovery(token) {
    return request('/api/v1/case-recovery/confirm', {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('recovery') }, body: JSON.stringify({ token }),
    });
  },
  retryCopyback(attemptId) {
    return request(`/api/v1/execution-attempts/${encodeURIComponent(attemptId)}/copyback`, {
      method: 'POST', headers: { 'Idempotency-Key': idempotencyKey('copyback') },
    });
  },
};
