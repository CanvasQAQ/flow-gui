import { lazy, Suspense, useMemo, useState } from 'react';
import {
  Alert, App, Badge, Button, Card, Checkbox, Col, Descriptions, Dropdown, Empty,
  Form, Input, InputNumber, Layout, Menu, Modal, Progress, Radio, Row, Segmented,
  Popover, Select, Space, Statistic, Steps, Switch, Table, Tabs, Tag, Timeline, Tooltip, Typography,
} from 'antd';
import {
  ApartmentOutlined,
  CloudServerOutlined, DatabaseOutlined, DownOutlined,
  FolderOpenOutlined, PauseCircleOutlined,
  PlayCircleOutlined, PlusOutlined, ReloadOutlined, RightOutlined,
  SettingOutlined, SyncOutlined,
} from '@ant-design/icons';
import { useApp, selectActiveRun, summarizeCorners } from '../store/appStore';
import { buildCases, CORNER_STATES, STAGES, STATE_META } from '../data/mockData';

const CornerWorkflowModal = lazy(() => import('./CornerWorkflowModal'));

const { Text, Title } = Typography;

const NAV_ITEMS = [
  { key: 'datasets', icon: <DatabaseOutlined />, label: 'Input Data' },
  { key: 'runs', icon: <ApartmentOutlined />, label: 'Runs' },
  { key: 'settings', icon: <SettingOutlined />, label: 'Settings' },
];

function StateTag({ state }) {
  const meta = STATE_META[state];
  return <Tag color={meta?.tone}>{meta?.label || state}</Tag>;
}

function RunStateTag({ status }) {
  const meta = status === 'completed'
    ? { color: 'success', label: 'Complete' }
    : status === 'running'
      ? { color: 'processing', label: 'Running' }
      : { color: 'warning', label: 'Paused' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

const BACKEND_STATE_META = {
  ready: { badge: 'success', label: 'Backend ready' },
  starting: { badge: 'processing', label: 'Backend starting' },
  degraded: { badge: 'warning', label: 'Backend degraded' },
  stopping: { badge: 'processing', label: 'Backend draining' },
  stopped: { badge: 'default', label: 'Backend stopped' },
  crashed: { badge: 'error', label: 'Backend crashed' },
};

const CHECKER_STATE_META = {
  healthy: { badge: 'success', label: 'Status checks normal' },
  normal: { badge: 'success', label: 'Status checks normal' },
  checking: { badge: 'processing', label: 'Checking task status' },
  delayed: { badge: 'warning', label: 'Status checks delayed' },
  error: { badge: 'error', label: 'Status check failed' },
  unknown: { badge: 'default', label: 'Status check pending' },
  idle: { badge: 'default', label: 'Status check pending' },
};

function BackendStatus() {
  const { state, actions } = useApp();
  const backendState = state.backend?.configured === false ? 'degraded' : state.backend?.state || 'degraded';
  const backendMeta = BACKEND_STATE_META[backendState] || BACKEND_STATE_META.degraded;
  const checkerState = state.checker?.state || state.checker?.status || 'unknown';
  const checkerMeta = CHECKER_STATE_META[checkerState] || CHECKER_STATE_META.unknown;
  const primary = backendState === 'ready' ? checkerMeta : backendMeta;
  const detail = state.backend?.mode === 'demo'
    ? `Demo Backend · ${state.backend.scenario || 'default'}`
    : state.dataMode === 'demo' ? 'Showing fallback demo data'
      : state.syncState === 'connected' ? 'Live updates connected' : 'Live updates reconnecting';
  const menu = { items: [
    { key: 'refresh', label: 'Refresh Backend data', onClick: () => actions.refresh().catch(() => {}) },
    { key: 'start', label: 'Start Backend', disabled: !window.flowGui?.backend || ['ready', 'starting'].includes(backendState), onClick: actions.backend.start },
    { key: 'restart', label: 'Restart Backend', disabled: !window.flowGui?.backend || state.backend?.managed === false, onClick: actions.backend.restart },
    { key: 'stop', label: 'Stop Backend', disabled: !window.flowGui?.backend || backendState === 'stopped' || state.backend?.managed === false, danger: true, onClick: actions.backend.stop },
    { type: 'divider' },
    { key: 'logs', label: 'Open Backend logs', disabled: !window.flowGui?.backend, onClick: actions.backend.openLogs },
  ] };
  return <Dropdown menu={menu} trigger={['click']}>
    <button type="button" className="backend-status backend-status-button" aria-label={`${primary.label}. ${detail}`}>
      <Badge status={primary.badge} />
      <div><strong>{primary.label}</strong><small>{detail}</small></div>
      <DownOutlined />
    </button>
  </Dropdown>;
}

export default function MainView() {
  const { state, dispatch } = useApp();
  const run = selectActiveRun(state);
  const [createOpen, setCreateOpen] = useState(false);
  const activeSection = state.activeSection === 'recovery' ? 'runs' : state.activeSection;

  return (
    <Layout className="workflow-shell">
      <Layout.Sider width={248} theme="light" className="workflow-sidebar">
        <div className="brand-block">
          <div className="brand-mark"><img src="/flowpilot-icon.svg" alt="" /></div>
          <div><strong>FlowPilot</strong><span>LSF simulation workflow</span></div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[activeSection]}
          items={NAV_ITEMS}
          onClick={({ key }) => dispatch({ type: 'SET_SECTION', value: key })}
          className="primary-nav"
        />
        <BackendStatus />
      </Layout.Sider>

      <Layout className="workflow-main">
        <Layout.Content className="workflow-content">
          {activeSection === 'datasets' && <DatasetsPage />}
          {activeSection === 'runs' && <RunPage run={run} onCreateRun={() => setCreateOpen(true)} />}
          {activeSection === 'settings' && <SettingsPage />}
        </Layout.Content>
      </Layout>
      <CreateRunModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </Layout>
  );
}

function PageHeading({ eyebrow, title, description, actions }) {
  return (
    <div className="page-heading">
      <div><span className="eyebrow">{eyebrow}</span><Title level={2}>{title}</Title><Text type="secondary">{description}</Text></div>
      {actions && <Space>{actions}</Space>}
    </div>
  );
}

function DatasetsPage() {
  const { state, actions } = useApp();
  const { message } = App.useApp();
  const [scanOpen, setScanOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [newDataset, setNewDataset] = useState({ name: '', path: '' });
  const [scanMode, setScanMode] = useState('append');
  const dataset = state.datasets[0];
  const confirmScan = async () => {
    try {
      await actions.scanDataset(dataset.id, scanMode);
      setScanOpen(false);
      message.success('Dataset scan accepted by Backend');
    } catch (error) { message.error(error.message); }
  };
  const createDataset = async () => {
    try { await actions.createDataset(newDataset); setAddOpen(false); message.success('Input dataset created'); }
    catch (error) { message.error(error.message); }
  };
  const addButton = <Button key="add" type="primary" icon={<FolderOpenOutlined />} onClick={() => setAddOpen(true)}>Add baseline directory</Button>;
  const addModal = <Modal open={addOpen} title="Add baseline directory" okText="Create input" onCancel={() => setAddOpen(false)} onOk={createDataset} okButtonProps={{ disabled: !newDataset.name.trim() || !newDataset.path.trim() }}><Form layout="vertical"><Form.Item label="Name" required><Input value={newDataset.name} onChange={(event) => setNewDataset({ ...newDataset, name: event.target.value })} /></Form.Item><Form.Item label="Baseline directory" required><Input value={newDataset.path} onChange={(event) => setNewDataset({ ...newDataset, path: event.target.value })} /></Form.Item></Form></Modal>;
  if (!dataset) return <><PageHeading eyebrow="STAGE 1 INPUT" title="Input Data" description="Add a baseline directory to begin." actions={[addButton]} /><Card><Empty description="No input datasets" /></Card>{addModal}</>;
  return (
    <>
      <PageHeading eyebrow="STAGE 1 INPUT" title="Input Data" description="Scan a baseline directory and preserve Corner, SP, MT, and immutable input versions for reuse across Runs." actions={[
        addButton,
      ]} />
      <Card className="dataset-card">
        <div className="dataset-main">
          <div className="dataset-icon"><DatabaseOutlined /></div>
          <div className="dataset-copy"><Title level={4}>{dataset.name}</Title><Text className="mono">{dataset.path}</Text><Text type="secondary">Last scan {dataset.scannedAt} · Current input version v{dataset.version}</Text></div>
          <Dropdown menu={{ items: [
            { key: 'append', label: 'Append scan (preserve valid Corners)', onClick: () => { setScanMode('append'); setScanOpen(true); } },
            { key: 'overwrite', label: 'Full rescan (parse everything)', danger: true, onClick: () => { setScanMode('overwrite'); setScanOpen(true); } },
          ] }}>
            <Button icon={<SyncOutlined />}>Rescan <DownOutlined /></Button>
          </Dropdown>
        </div>
        <Row gutter={16} className="dataset-stats">
          <Col span={6}><Statistic title="Discovered Corners" value={dataset.total} /></Col>
          <Col span={6}><Statistic title="Available for new Runs" value={dataset.available} valueStyle={{ color: '#15803d' }} /></Col>
          <Col span={6}><Statistic title="Parse errors" value={dataset.invalid} valueStyle={{ color: '#c2410c' }} /></Col>
          <Col span={6}><Statistic title="Missing files" value={dataset.missing} valueStyle={{ color: '#64748b' }} /></Col>
        </Row>
        {(dataset.invalid + dataset.missing) > 0 && <Alert type="warning" showIcon message={`${dataset.invalid + dataset.missing} Corners are currently unavailable`} description="Individual errors do not stop initialization. Repair the baseline directory and run an append scan to retry invalid entries." action={<Button size="small">View issues</Button>} />}
      </Card>
      <Modal open={scanOpen} title="Rescan baseline directory" okText={`Start ${scanMode} scan`} cancelText="Cancel" onCancel={() => setScanOpen(false)} onOk={confirmScan}>
        <Alert type="info" showIcon message="Append scan is recommended" description="Valid Corners and historical input versions remain unchanged. The scan discovers new Corners and retries previous errors without changing existing Runs." />
        <Radio.Group value={scanMode} onChange={(event) => setScanMode(event.target.value)} className="scan-options">
          <Radio value="append"><strong>Append scan</strong><span>Preserve valid entries and process new or invalid entries</span></Radio>
          <Radio value="overwrite"><strong>Full rescan</strong><span>Parse everything and version changed inputs</span></Radio>
        </Radio.Group>
      </Modal>
      {addModal}
    </>
  );
}

function RunPage({ run, onCreateRun }) {
  const { state, actions } = useApp();
  const { message } = App.useApp();
  const [runSettingsOpen, setRunSettingsOpen] = useState(false);
  const summary = summarizeCorners(run.corners || []);
  const recoveryCount = useMemo(() => buildCases(run).length, [run]);
  const progress = getCompletionPercent(summary);
  const [tab, setTab] = useState('overview');
  const togglePause = async () => {
    const next = run.status === 'running' ? 'paused' : 'running';
    try {
      await actions.setRunPaused(run.id, next === 'paused');
      message[next === 'paused' ? 'warning' : 'success'](next === 'paused'
        ? 'New work is paused. Submitted Jobs continue running and reporting results.'
        : 'Run resumed. The Backend will continue from the next safe step.');
    } catch (error) { message.error(error.message); }
  };
  if (!run.id) return <><PageHeading eyebrow="RUNS" title="Runs" description="Create a Run after adding input data." actions={[<Button key="create" type="primary" icon={<PlusOutlined />} onClick={onCreateRun}>Create Run</Button>]} /><Card><Empty description="No Runs" /></Card></>;
  return (
    <div className="run-page">
      <RunBrowserTabs runs={state.runs} activeRun={run} onCreateRun={onCreateRun} />
      <header className="run-command-bar">
        <div className="run-command-identity">
          <div className="run-title-line"><Title level={2}>{run.name}</Title><RunStateTag status={run.status} /></div>
          <Text type="secondary" title={`Created ${run.createdAt}`}>{run.id} · {run.datasetName}</Text>
        </div>
        <div className="run-command-meta" aria-label="Run configuration">
          <span><small>VDD</small><strong>{run.vdd}</strong></span>
          <span><small>VCM</small><strong>{run.vcm}</strong></span>
          <span className="run-algorithm"><small>Algorithm</small><strong>{run.algorithm}</strong></span>
          <span><small>Corners</small><strong>{run.corners.length}</strong></span>
        </div>
        <div className="run-command-progress">
          <span title={`${summary.done.toLocaleString()} of ${summary.total.toLocaleString()} Corners complete`}><small>Completion</small><strong>{progress}%</strong></span>
          <Progress percent={progress} showInfo={false} size="small" strokeColor="#4f8a68" />
        </div>
        <Space className="run-command-actions" size={8}>
          <Button icon={<SettingOutlined />} onClick={() => setRunSettingsOpen(true)}>Run Settings</Button>
          <Button type={run.status === 'running' ? 'default' : 'primary'} icon={run.status === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />} onClick={togglePause}>{run.status === 'running' ? 'Pause Run' : 'Resume Run'}</Button>
        </Space>
      </header>
      <div className="run-page-body">
        <Tabs activeKey={tab} onChange={setTab} className="run-tabs" items={[
          { key: 'overview', label: 'Live overview', children: <RunOverview run={run} /> },
          { key: 'corners', label: `Corner list (${summary.total})`, children: <CornerTable run={run} /> },
          { key: 'recovery', label: `Recovery (${recoveryCount})`, children: <RecoveryTab key={run.id} run={run} /> },
          { key: 'activity', label: 'Activity', children: <ActivityLog /> },
        ]} />
      </div>
      <RunSettingsModal open={runSettingsOpen} onClose={() => setRunSettingsOpen(false)} run={run} />
    </div>
  );
}

function RunBrowserTabs({ runs, activeRun, onCreateRun }) {
  const { dispatch, actions } = useApp();
  const { message } = App.useApp();
  const refreshRun = (run) => actions.refresh().then(() => message.success(`${run.name}: latest Backend snapshot loaded`)).catch((error) => message.error(error.message));
  return (
    <div className="run-browser-tabs" role="tablist" aria-label="Open Runs">
      {runs.map((item) => {
        const summary = summarizeCorners(item.corners);
        const active = item.id === activeRun.id;
        return (
          <div className={`run-browser-tab ${active ? 'is-active' : ''}`} key={item.id}>
            <button type="button" role="tab" aria-selected={active} onClick={() => dispatch({ type: 'SET_ACTIVE_RUN', id: item.id })}>
              <span className={`run-dot ${item.status}`} />
              <span><strong>{item.name}</strong><small>{summary.done}/{summary.total}</small></span>
            </button>
            <Tooltip title="Refresh this Run">
              <button type="button" className="run-tab-refresh" aria-label={`Refresh ${item.name}`} onClick={() => refreshRun(item)}><ReloadOutlined /></button>
            </Tooltip>
          </div>
        );
      })}
      <Tooltip title="Create Run">
        <button type="button" className="run-tab-add" aria-label="Create Run" onClick={onCreateRun}><PlusOutlined /></button>
      </Tooltip>
    </div>
  );
}

function RunOverview({ run }) {
  const summary = summarizeCorners(run.corners);
  const stages = STAGES.map((stage) => ({ ...stage, count: run.corners.filter((corner) => corner.stage === stage.key && corner.state !== CORNER_STATES.DONE).length }));
  return (
    <div className="run-overview-layout">
      <RunSummary summary={summary} corners={run.corners} stages={stages} />
      <CornerReviewMap run={run} corners={run.corners} />
    </div>
  );
}

function RunSummary({ summary, corners, stages }) {
  const total = corners.length;
  const completePercent = getCompletionPercent(summary);
  const states = [
    { label: 'Running', value: summary.active, tone: 'running' },
    { label: 'Needs action', value: summary.attention, tone: 'action' },
    { label: 'Complete', value: summary.done, tone: 'complete' },
    { label: 'Pending', value: summary.not_started, tone: 'pending' },
  ];
  const segments = [
    ...stages.map((stage, index) => ({ ...stage, color: ['#a7bce8', '#6f91d7', '#3f6fbd'][index] })),
    { key: 'done', name: 'Done', count: summary.done, color: '#4f8a68' },
  ];
  return (
    <div className="run-summary-panel">
      <div className="run-summary-overall" title={`${summary.done.toLocaleString()} of ${total.toLocaleString()} Corners complete`}>
        <span>Run completion</span><strong>{completePercent}%</strong>
      </div>
      <Progress percent={completePercent} showInfo={false} size="small" strokeColor="#4f8a68" />
      <div className="run-summary-states">
        {states.map((item) => <div key={item.label}><span className={`summary-state-dot ${item.tone}`} /><strong>{item.value.toLocaleString()}</strong><small>{item.label}</small></div>)}
      </div>
      <div className="run-summary-divider" />
      <span className="run-summary-label">Stage distribution</span>
      <div className="stage-mini-bar" aria-label={`Stage distribution for ${total} Corners`}>
        {segments.map((segment) => <span key={segment.key} style={{ background: segment.color, flexGrow: Math.max(segment.count, 1) }} title={`${segment.name}: ${segment.count}`} />)}
      </div>
      <div className="stage-mini-grid">
        {segments.map((segment) => <div key={segment.key}><span style={{ background: segment.color }} /><small>{segment.name}</small><strong>{segment.count}</strong></div>)}
      </div>
    </div>
  );
}

function getCompletionPercent(summary) {
  return summary.total ? Math.round((summary.done / summary.total) * 100) : 0;
}

const MAP_SIZE_PRESETS = {
  small: { cols: 80, cell: 7, gap: 2 },
  medium: { cols: 64, cell: 9, gap: 3 },
  large: { cols: 50, cell: 12, gap: 3 },
};

const ACTION_STATES = new Set([CORNER_STATES.WAITING_USER, CORNER_STATES.STATUS_UNKNOWN, CORNER_STATES.FAILED]);
const ACTIVE_STATES = new Set([CORNER_STATES.PREPARING, CORNER_STATES.QUEUED, CORNER_STATES.RUNNING, CORNER_STATES.POSTPROCESS]);
const RUN_PROGRESS_COLORS = ['#c8d8f0', '#94b2df', '#5f8dcc', '#3268ae'];

function cornerRunProgress(corner) {
  if (corner.state === CORNER_STATES.DONE) return 100;
  if (corner.state === CORNER_STATES.NOT_STARTED || corner.state === CORNER_STATES.IGNORED) return 0;
  const stageBase = { stage1: 0, stage2: 33, stage3: 66 }[corner.stage] || 0;
  const testcaseRatio = corner.testcaseTotal ? corner.testcaseDone / corner.testcaseTotal : 0;
  const phase = {
    [CORNER_STATES.PREPARING]: 0.08,
    [CORNER_STATES.QUEUED]: 0.2,
    [CORNER_STATES.RUNNING]: 0.28 + testcaseRatio * 0.45,
    [CORNER_STATES.POSTPROCESS]: 0.86,
  }[corner.state] ?? 0.5;
  return Math.min(99, Math.round(stageBase + phase * 33));
}

function cornerMapVisual(corner) {
  if (corner.state === CORNER_STATES.DONE) return { group: 'Complete', color: '#4f8a68', progress: 100 };
  if (ACTION_STATES.has(corner.state)) return { group: 'Needs action', color: '#cf6546', progress: cornerRunProgress(corner) };
  if (!ACTIVE_STATES.has(corner.state)) return { group: 'Pending', color: '#d8dee7', progress: 0 };
  const progress = cornerRunProgress(corner);
  const shade = Math.min(RUN_PROGRESS_COLORS.length - 1, Math.floor(progress / 25));
  return { group: 'Running', color: RUN_PROGRESS_COLORS[shade], progress };
}

function CornerReviewMap({ run, corners }) {
  const [detail, setDetail] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [cellSize, setCellSize] = useState('large');
  const [groupSize, setGroupSize] = useState(330);
  const { cols, cell, gap } = MAP_SIZE_PRESETS[cellSize];
  const labelWidth = 34;
  const matrixWidth = cols * (cell + gap) + gap;
  const width = matrixWidth + labelWidth;
  const summary = summarizeCorners(corners);
  const groups = useMemo(() => Array.from({ length: Math.ceil(corners.length / groupSize) }, (_, index) => {
    const startIndex = index * groupSize;
    return { index, startIndex, corners: corners.slice(startIndex, startIndex + groupSize) };
  }), [corners, groupSize]);
  const settings = (
    <div className="corner-map-settings">
      <div><span>Cell size</span><Segmented size="small" aria-label="Corner cell size" value={cellSize} onChange={setCellSize} options={[{ value: 'small', label: 'S' }, { value: 'medium', label: 'M' }, { value: 'large', label: 'L' }]} /></div>
      <div><span>Corners per group</span><InputNumber aria-label="Corners per group" min={50} max={1000} step={10} value={groupSize} onChange={(value) => setGroupSize(value || 330)} /></div>
    </div>
  );
  return (
    <Card
      className="corner-map-card"
      title={<strong className="corner-map-heading">Corner status map</strong>}
      extra={<div className="corner-map-head-actions"><span className="corner-map-total">{corners.length.toLocaleString()} Corners · {groups.length} groups</span><Popover placement="bottomRight" trigger="click" title="Map settings" content={settings}><Button type="text" size="small" icon={<SettingOutlined />} aria-label="Corner map settings" /></Popover></div>}
    >
      <div className="corner-map-toolbar">
        <div className="corner-map-legends" aria-label="Corner status legend">
          <span><i className="legend-running" />Running <strong>{summary.active}</strong><small>progress</small></span>
          <span><i className="legend-action" />Needs action <strong>{summary.attention}</strong></span>
          <span><i className="legend-complete" />Complete <strong>{summary.done}</strong></span>
          <span><i className="legend-pending" />Pending <strong>{summary.not_started}</strong></span>
        </div>
      </div>
      <div className="corner-map-canvas" onMouseLeave={() => setHovered(null)}>
        {groups.map((group) => {
          const rows = Math.ceil(group.corners.length / cols);
          const height = rows * (cell + gap) + gap;
          const first = group.startIndex + 1;
          const last = group.startIndex + group.corners.length;
          return (
            <section className="corner-map-group" key={first}>
              <header><strong>Group {group.index + 1}</strong><span>Corner {first}–{last}</span><small>{group.corners.length} Corners</small></header>
              <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Corner ${first} to ${last} status matrix`}>
                {group.corners.map((corner, index) => {
                  const x = gap + (index % cols) * (cell + gap);
                  const y = gap + Math.floor(index / cols) * (cell + gap);
                  const visual = cornerMapVisual(corner);
                  return (
                    <rect
                      key={corner.id}
                      x={x} y={y} width={cell} height={cell} rx={Math.max(1.5, cell * 0.18)}
                      fill={visual.color}
                      className="corner-map-cell is-visible"
                      onMouseEnter={(event) => setHovered({ corner, visual, x: event.clientX, y: event.clientY })}
                      onClick={() => setDetail(corner)}
                      tabIndex={0}
                      role="button"
                      aria-label={`${corner.id}, ${visual.group}, ${visual.progress}% progress`}
                      onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && setDetail(corner)}
                    />
                  );
                })}
                {Array.from({ length: rows }, (_, row) => {
                  const end = group.startIndex + Math.min((row + 1) * cols, group.corners.length);
                  return <text className="corner-row-label" key={end} x={matrixWidth + 5} y={gap + row * (cell + gap) + cell * 0.72}>{end}</text>;
                })}
              </svg>
            </section>
          );
        })}
        {hovered && <div className="corner-map-tooltip" style={{ left: hovered.x, top: hovered.y }}><strong>{hovered.corner.id}</strong><StateTag state={hovered.corner.state} /><span>{hovered.visual.group} · {hovered.visual.progress}% run progress</span><span>{STAGES.find((stage) => stage.key === hovered.corner.stage)?.name} · Testcases {hovered.corner.testcaseDone}/{hovered.corner.testcaseTotal}</span>{hovered.corner.issue && <small>{hovered.corner.issue}</small>}</div>}
      </div>
      {detail && <Suspense fallback={null}><CornerWorkflowModal key={detail.id} corner={detail} run={run} open onClose={() => setDetail(null)} /></Suspense>}
    </Card>
  );
}

function CornerTable({ run, compact = false }) {
  const [detail, setDetail] = useState(null);
  const [stateFilter, setStateFilter] = useState('all');
  const [query, setQuery] = useState('');
  const rows = useMemo(() => run.corners.filter((corner) => {
    const matchesState = stateFilter === 'all' || corner.state === stateFilter;
    const matchesQuery = !query || corner.id.includes(query.trim()) || String(corner.number) === query.trim();
    return matchesState && matchesQuery;
  }).slice(0, compact ? 8 : undefined), [run.corners, stateFilter, query, compact]);
  const columns = [
    { title: 'Corner', dataIndex: 'id', render: (value, row) => <button type="button" className="table-link" onClick={() => setDetail(row)}>{value}</button> },
    { title: 'Status', dataIndex: 'state', width: 126, render: (value) => <StateTag state={value} /> },
    { title: 'Active path', dataIndex: 'stage', width: 150, render: (value, row) => <span><strong>{STAGES.find((stage) => stage.key === value)?.name}</strong> · Round {row.round}</span> },
    { title: 'Current group', width: 150, render: (_, row) => row.testcaseTotal ? `${row.testcaseDone} / ${row.testcaseTotal}` : 'Not generated' },
    { title: 'Input version', dataIndex: 'inputVersion', width: 120, render: (value, row) => <Space size={4}><Tag>{value}</Tag>{row.updateAvailable && <Tooltip title="A newer baseline version exists. This Run still uses its pinned input version."><Badge status="warning" /></Tooltip>}</Space> },
    { title: 'Final Loss', dataIndex: 'loss', width: 110, render: (value) => value ?? '—' },
    { title: 'Updated', dataIndex: 'updatedAt', width: 120, className: 'muted-cell' },
    { title: '', width: 56, render: (_, row) => <Button type="text" icon={<RightOutlined />} aria-label={`View ${row.id}`} onClick={() => setDetail(row)} /> },
  ];
  return (
    <Card className="corner-list-card" title={compact ? 'Recently updated Corners' : 'Corner monitor'} extra={compact ? null : <Space><Input.Search placeholder="Number, e.g. 12 or corner_0012" allowClear onSearch={setQuery} style={{ width: 245 }} /><Select value={stateFilter} onChange={setStateFilter} style={{ width: 150 }} options={[{ value: 'all', label: 'All statuses' }, ...Object.entries(STATE_META).map(([value, meta]) => ({ value, label: meta.label }))]} /></Space>}>
      <Table rowKey="id" columns={columns} dataSource={rows} size="middle" pagination={compact ? false : { pageSize: 12, showSizeChanger: false, showTotal: (total) => `${total} Corners` }} />
      {detail && <Suspense fallback={null}><CornerWorkflowModal key={detail.id} corner={detail} run={run} open onClose={() => setDetail(null)} /></Suspense>}
    </Card>
  );
}

function RecoveryTab({ run }) {
  const { state, actions } = useApp();
  const { message } = App.useApp();
  const cases = useMemo(() => buildCases(run), [run]);
  const [selected, setSelected] = useState([]);
  const [stateFilter, setStateFilter] = useState('all');
  const [action, setAction] = useState(null);
  const filtered = cases.filter((item) => stateFilter === 'all' || item.state === stateFilter);
  const selectedRows = cases.filter((item) => selected.includes(item.key));
  const copybackSelection = selectedRows.length > 0 && selectedRows.every((item) => item.state === CORNER_STATES.COPYBACK_WAITING && item.attemptId);
  const columns = [
    { title: 'Corner / Testcase', render: (_, row) => <div><strong>{row.cornerId}</strong><small className="cell-subtitle">{row.testcase}</small></div> },
    { title: 'Stage / Algorithm call', render: (_, row) => `${STAGES.find((stage) => stage.key === row.stage)?.name} · #${row.algorithmCall}` },
    { title: 'Status', dataIndex: 'state', render: (value) => <StateTag state={value} /> },
    { title: 'LSF Job', dataIndex: 'lsfJob', className: 'mono' },
    { title: 'Evidence', dataIndex: 'evidence' },
    { title: 'Attempts', dataIndex: 'attempt', render: (value) => `${value}` },
    { title: 'Updated', dataIndex: 'updatedAt', className: 'muted-cell' },
  ];
  const confirmAction = async () => {
    if (state.dataMode !== 'live') {
      message.warning('Recovery actions require a live Backend connection');
      return;
    }
    const testcaseIds = selectedRows.map((row) => row.testcaseId).filter(Boolean);
    if (testcaseIds.length !== selectedRows.length) {
      message.error('Backend snapshot does not contain stable Testcase IDs');
      return;
    }
    try {
      const preview = await actions.previewRecovery(run.id, action === 'retry' ? 'resubmit' : 'ignore', { testcaseIds });
      await actions.confirmRecovery(preview.token);
      message.success(action === 'retry' ? `New attempts created for ${selected.length} Testcases` : `${selected.length} Testcases ignored`);
      setAction(null); setSelected([]);
    } catch (error) { message.error(error.message); }
  };
  const retryCopyback = async () => {
    try {
      await Promise.all(selectedRows.map((row) => actions.retryCopyback(row.attemptId)));
      message.success(`Copy-back queued for ${selectedRows.length} Attempts`);
      setSelected([]);
    } catch (error) { message.error(error.message); }
  };
  return (
    <div className="recovery-tab">
      <div className="recovery-context">
        <div><strong>Case Recovery</strong><Text type="secondary">Review Testcases that need a decision in {run.name}. Failed work is never retried automatically.</Text></div>
        <Tag color="warning">{cases.length} need review</Tag>
      </div>
      <Card className="recovery-card">
        <div className="filter-bar">
          <Input.Search placeholder="Corner, Testcase, or LSF Job" style={{ width: 280 }} />
          <Select value={stateFilter} onChange={setStateFilter} style={{ width: 180 }} options={[
            { value: 'all', label: 'All review statuses' },
            { value: 'failed', label: 'Failed' },
            { value: 'status_unknown', label: 'Status unknown' },
            { value: 'copyback_waiting', label: 'Result copy failed' },
          ]} />
          <Select defaultValue="all" style={{ width: 150 }} options={[{ value: 'all', label: 'All Stages' }, ...STAGES.map((stage) => ({ value: stage.key, label: stage.name }))]} />
          <div className="filter-actions"><Button disabled={!copybackSelection} onClick={retryCopyback}>Retry copy</Button><Button disabled={!selected.length} onClick={() => setAction('ignore')}>Ignore results</Button><Button type="primary" danger disabled={!selected.length} onClick={() => setAction('retry')}>Resimulate</Button></div>
        </div>
        {!!selected.length && <div className="selection-banner"><Checkbox checked /><strong>{selected.length}</strong> items selected on this page.<Button type="link">Select all {filtered.length} matching Cases</Button></div>}
        <Table rowKey="key" dataSource={filtered} columns={columns} rowSelection={{ selectedRowKeys: selected, onChange: setSelected }} pagination={{ pageSize: 12, showSizeChanger: false, showTotal: (total) => `${total} Cases need review` }} />
      </Card>
      <Modal open={!!action} onCancel={() => setAction(null)} onOk={confirmAction} okText={action === 'retry' ? 'Create and submit new Batches' : 'Confirm ignore'} okButtonProps={{ danger: action === 'retry' }} title={action === 'retry' ? 'Resimulation preview' : 'Ignore Testcases'}>
        {action === 'retry' ? <>
          <Alert type="warning" showIcon message="Confirm that old Jobs will not continue writing to the same Case directories" description="A Job with unknown status may still be running. Resubmission does not remove old waveforms or MT files and may overwrite or mix outputs." />
          <Descriptions className="action-preview" column={1} bordered size="small" items={[
            { key: 'selected', label: 'Selected Testcases', children: selected.length },
            { key: 'batches', label: 'Estimated new Batches', children: `${new Set(selectedRows.map((row) => row.cornerId)).size}, grouped by Corner` },
            { key: 'attempt', label: 'Execution records', children: 'One new attempt per item; previous history is preserved' },
            { key: 'directory', label: 'Work directories', children: 'Reuse each Testcase fixed Case directory' },
          ]} />
        </> : <Alert type="warning" showIcon message="Ignored Testcases no longer block their algorithm-call group" description="Original records, logs, and files remain. After other Testcases are resolved, the Corner can continue to Postprocess and algorithm evaluation." />}
      </Modal>
    </div>
  );
}

function ActivityLog() {
  return <Card><Timeline items={[
    { color: 'blue', children: <><strong>16:42 · Status checker read a new public snapshot</strong><p>84 Array elements updated: 63 RUN, 18 PEND, and 3 EXIT.</p></> },
    { color: 'green', children: <><strong>16:40 · Batch Postprocess completed for 12 Corners</strong><p>Four-phase Loss values were saved and algorithm calls were triggered independently.</p></> },
    { color: 'blue', children: <><strong>16:38 · 9 new Batches created</strong><p>Generated by the next algorithm calls for 9 independent Corners.</p></> },
    { color: 'orange', children: <><strong>16:31 · 4 Cases entered status unknown</strong><p>Absence from one snapshot is not failure. They will be checked against the next snapshot.</p></> },
  ]} /></Card>;
}

function SettingsPage() {
  const { state, dispatch } = useApp();
  return <>
    <PageHeading eyebrow="WORKFLOW CONFIGURATION" title="Settings" description="Configure the interface, future Batch submissions, and local Postprocess behavior." />
    <Card title="Interface" className="interface-settings-card">
      <div className="setting-row">
        <div><strong>Interface animations</strong><Text type="secondary">Enable transitions for dialogs, tabs, menus, and other interface changes. Loading and running status indicators remain animated.</Text></div>
        <Switch checked={state.animationsEnabled} onChange={(value) => dispatch({ type: 'SET_ANIMATIONS_ENABLED', value })} aria-label="Enable interface animations" />
      </div>
    </Card>
    <Row gutter={16}>
      <Col span={14}><Card title="LSF submission"><Form layout="vertical"><Row gutter={16}><Col span={12}><Form.Item label="Queue"><Input defaultValue="normal" /></Form.Item></Col><Col span={12}><Form.Item label="Project"><Input defaultValue="adc_calibration" /></Form.Item></Col></Row><Form.Item label="Public status snapshot"><Input defaultValue="/it/lsf/status/bjobs.parquet" prefix={<CloudServerOutlined />} /></Form.Item><Form.Item label="Batch name prefix"><Input defaultValue="flowpilot" /></Form.Item><Button type="primary">Save settings</Button></Form></Card></Col>
      <Col span={10}><Card title="Local Postprocess"><Form layout="vertical"><Form.Item label="Maximum concurrency"><InputNumber min={1} max={16} defaultValue={4} style={{ width: '100%' }} /></Form.Item><Form.Item label="Result script"><Input defaultValue="/tools/postprocess/run.py" /></Form.Item></Form><Alert type="info" showIcon message="Postprocess starts per Corner algorithm-call group" description="A single Batch completion does not trigger it. Local concurrency is limited here." /></Card></Col>
    </Row>
  </>;
}

function parseCornerNumbers(value) {
  const numbers = new Set();
  for (const token of String(value).split(',').map((item) => item.trim()).filter(Boolean)) {
    const match = token.match(/^(\d+)(?:-(\d+))?$/);
    if (!match) throw new Error(`Invalid Corner range: ${token}`);
    const start = Number(match[1]);
    const end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end - start > 100_000) throw new Error(`Invalid Corner range: ${token}`);
    for (let number = start; number <= end; number += 1) numbers.add(number);
  }
  if (!numbers.size) throw new Error('Enter at least one Corner number');
  return [...numbers].sort((a, b) => a - b);
}

function CreateRunModal({ open, onClose }) {
  const { state, actions } = useApp();
  const { message } = App.useApp();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [draft, setDraft] = useState({ name: 'VT drift · new run', vdd: '0.72', vcm: '0.36', algorithm: 'reference-search', corners: '1-1248' });
  const close = () => { setStep(0); onClose(); };
  const next = () => setStep((value) => Math.min(value + 1, 2));
  const finish = async () => {
    try {
      setSubmitting(true);
      await actions.createRun({ datasetId: state.datasets[0]?.id, name: draft.name, pvt: { vdd: draft.vdd, vcm: draft.vcm }, algorithmScheme: draft.algorithm, algorithmConfig: {}, cornerNumbers: parseCornerNumbers(draft.corners) });
      message.success('Run accepted by Backend. Every Corner will start independently.');
      close();
    } catch (error) { message.error(error.message); }
    finally { setSubmitting(false); }
  };
  return <Modal open={open} onCancel={close} width={760} title="Create Run" footer={<Space><Button onClick={close}>Cancel</Button>{step > 0 && <Button onClick={() => setStep(step - 1)}>Back</Button>}<Button type="primary" loading={submitting} onClick={step === 2 ? finish : next}>{step === 2 ? 'Confirm and start' : 'Next'}</Button></Space>}>
    <Steps current={step} items={[{ title: 'Select input' }, { title: 'Configure Run' }, { title: 'Confirm scope' }]} className="modal-steps" />
    {step === 0 && <Form layout="vertical"><Form.Item label="Input Data"><Select value={state.datasets[0]?.id} options={state.datasets.map((dataset) => ({ value: dataset.id, label: `${dataset.name} · ${dataset.available} usable Corners` }))} /></Form.Item><Alert type="info" showIcon message="The Run pins its input version" description="Once added, a Corner continues to reference the same original SP, initial Code, and MT snapshot. Later scans never replace it silently." /></Form>}
    {step === 1 && <Form layout="vertical"><Form.Item label="Run name" required><Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></Form.Item><Row gutter={16}><Col span={8}><Form.Item label="VDD" required><Input value={draft.vdd} onChange={(event) => setDraft({ ...draft, vdd: event.target.value })} suffix="V" /></Form.Item></Col><Col span={8}><Form.Item label="VCM" required><Input value={draft.vcm} onChange={(event) => setDraft({ ...draft, vcm: event.target.value })} suffix="V" /></Form.Item></Col><Col span={8}><Form.Item label="Algorithm plan" required><Select value={draft.algorithm} onChange={(algorithm) => setDraft({ ...draft, algorithm })} options={[{ value: 'reference-search', label: 'Reference Search' }]} /></Form.Item></Col></Row><Alert type="warning" showIcon message="One Run uses exactly one PVT condition" description="Create another Run from the same Input Data to compare a different VDD or VCM." /></Form>}
    {step === 2 && <><Form layout="vertical"><Form.Item label="Included Corner numbers"><Input value={draft.corners} onChange={(event) => setDraft({ ...draft, corners: event.target.value })} extra="Supports lists and ranges such as 1,2,3-5,6-20" /></Form.Item></Form></>}
  </Modal>;
}

function RunSettingsModal({ open, onClose, run }) {
  const { actions } = useApp();
  const { message } = App.useApp();
  const [range, setRange] = useState('1249-1272');
  const [submitting, setSubmitting] = useState(false);
  const add = async () => {
    try { setSubmitting(true); await actions.addCorners(run.id, parseCornerNumbers(range)); message.success('New Corners accepted by Backend.'); onClose(); }
    catch (error) { message.error(error.message); }
    finally { setSubmitting(false); }
  };
  return <Modal open={open} onCancel={onClose} width={620} title="Run Settings" okText="Add Corners" confirmLoading={submitting} onOk={add}>
    <Descriptions className="run-settings-summary" bordered size="small" column={2} items={[
      { key: 'run', label: 'Run', span: 2, children: run.name },
      { key: 'vdd', label: 'VDD', children: run.vdd },
      { key: 'vcm', label: 'VCM', children: run.vcm },
      { key: 'algorithm', label: 'Algorithm', children: run.algorithm },
      { key: 'corners', label: 'Corners', children: run.corners.length.toLocaleString() },
    ]} />
    <div className="run-settings-section"><Text strong>Extend Run scope</Text><Text type="secondary">New Corners use the same pinned input, PVT configuration, and algorithm. Existing Corners continue without interruption.</Text></div>
    <Form layout="vertical" className="add-corner-form"><Form.Item label="Corner numbers or ranges"><Input value={range} onChange={(event) => setRange(event.target.value)} extra="Existing Corners are not added twice. Unavailable Corners are explained by Backend." /></Form.Item></Form>
  </Modal>;
}
