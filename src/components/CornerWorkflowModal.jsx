import { useMemo, useState } from 'react';
import {
  Alert, App, Button, Card, Descriptions, Modal, Progress, Segmented, Space, Table,
  Tabs, Tag, Tooltip, Typography,
} from 'antd';
import {
  BarChartOutlined, CheckCircleFilled, CloudUploadOutlined,
  CodeOutlined, CopyOutlined, FileTextOutlined, LoadingOutlined, PlayCircleOutlined,
  RightOutlined, SafetyCertificateOutlined, SyncOutlined, WarningOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { CORNER_STATES, STATE_META, STAGES } from '../data/mockData';

const { Text, Title } = Typography;

const STEP_DEFINITIONS = [
  { key: 'generate', label: 'Generate testcases', description: 'Algorithm input and output are recorded', icon: <CodeOutlined /> },
  { key: 'prepare', label: 'Prepare Case and SP', description: 'Fixed directories and complete parameters', icon: <FileTextOutlined /> },
  { key: 'submit', label: 'Submit Batch', description: 'Execution attempts mapped to Array elements', icon: <CloudUploadOutlined /> },
  { key: 'simulate', label: 'Simulation group', description: 'Wait for the entire algorithm-call group', icon: <PlayCircleOutlined /> },
  { key: 'resolve', label: 'Resolve exceptions', description: 'Failed or unknown cases require a decision', icon: <SafetyCertificateOutlined /> },
  { key: 'postprocess', label: 'Batch Postprocess', description: 'Scan the active Stage execution directory', icon: <SyncOutlined /> },
  { key: 'decide', label: 'Analyze and decide', description: 'Use all valid Stage results', icon: <BarChartOutlined /> },
];

const SERVER_STEP_KEYS = {
  algorithm_decision: ['generate', 'decide'],
  generate_testcases: ['generate'],
  prepare_cases: ['prepare'],
  submit_batch: ['submit'],
  simulate_group: ['simulate'],
  recovery_gate: ['resolve'],
  postprocess: ['postprocess'],
};

function serverStepsFor(corner, selectedStage) {
  const source = corner.workflowSteps;
  if (!source) return null;
  if (Array.isArray(source)) return source;
  if (Array.isArray(source[selectedStage])) return source[selectedStage];
  if (Array.isArray(source.items)) return source.items;
  return null;
}

function uiStepState(value) {
  return ({ ongoing: 'running', attention: 'error', complete: 'complete', waiting: 'waiting', skipped: 'skipped' })[value] || value || 'waiting';
}

const RESULT_RENDERERS = {
  'Adaptive Search A': AlgorithmAResults,
  'Adaptive Search B': AlgorithmBResults,
};

function StateTag({ state }) {
  const meta = STATE_META[state];
  return <Tag color={meta?.tone}>{meta?.label || state}</Tag>;
}

export default function CornerWorkflowModal({ corner, run, open, onClose }) {
  const activeStageIndex = corner ? STAGES.findIndex((stage) => stage.key === corner.stage) : 0;
  const [selectedStage, setSelectedStage] = useState(corner?.stage || STAGES[0].key);

  if (!corner) return null;
  const selectedStageIndex = STAGES.findIndex((stage) => stage.key === selectedStage);
  const ResultRenderer = RESULT_RENDERERS[run?.algorithm] || AlgorithmAResults;

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={1240}
      footer={null}
      destroyOnHidden
      centered
      className="corner-workflow-modal"
      title={<div className="corner-modal-title"><div className="corner-modal-identity"><strong>{corner.id}</strong><span>Corner workflow and evidence</span></div><Space className="corner-modal-meta" size={6}><StateTag state={corner.state} /><Tag>{STAGES[activeStageIndex]?.name}</Tag><Tag>Input {corner.inputVersion}</Tag></Space></div>}
    >
      {corner.issue && <Alert role="alert" type={corner.state === CORNER_STATES.STATUS_UNKNOWN ? 'warning' : 'error'} showIcon message={corner.issue} className="corner-modal-alert" />}
      <Tabs
        defaultActiveKey="workflow"
        className="corner-modal-tabs"
        items={[
          { key: 'workflow', label: 'Workflow', children: <WorkflowTab key={selectedStage} corner={corner} selectedStage={selectedStage} onStageChange={setSelectedStage} /> },
          { key: 'info', label: 'Info', children: <InfoTab corner={corner} run={run} /> },
          { key: 'results', label: 'Results', children: <ResultRenderer corner={corner} run={run} selectedStage={selectedStage} onStageChange={setSelectedStage} selectedStageIndex={selectedStageIndex} /> },
          { key: 'testcases', label: `Testcases (${getTestcases(corner).length})`, children: <TestcasesTab corner={corner} selectedStage={selectedStage} onStageChange={setSelectedStage} /> },
        ]}
      />
    </Modal>
  );
}

function WorkflowTab({ corner, selectedStage, onStageChange }) {
  const activeStageIndex = STAGES.findIndex((stage) => stage.key === corner.stage);
  const selectedStageIndex = STAGES.findIndex((stage) => stage.key === selectedStage);
  const stageState = selectedStageIndex < activeStageIndex || corner.state === CORNER_STATES.DONE
    ? 'complete'
    : selectedStageIndex === activeStageIndex ? 'active' : 'waiting';
  const currentStep = getCurrentStep(corner, stageState);
  const roundCount = stageState === 'waiting' ? 1 : stageState === 'complete' ? Math.max(2, corner.round) : Math.max(1, corner.round);
  const [selectedRound, setSelectedRound] = useState(roundCount);
  const backendSteps = stageState === 'active' && selectedRound === roundCount ? serverStepsFor(corner, selectedStage) : null;
  const steps = STEP_DEFINITIONS.map((step, index) => {
    const matches = backendSteps?.filter((item) => (SERVER_STEP_KEYS[item.key] || [item.key]).includes(step.key));
    const serverStep = matches?.find((item) => item.key !== 'algorithm_decision') || matches?.[step.key === 'decide' ? matches.length - 1 : 0];
    return {
    ...step,
    serverDetail: serverStep?.summary || serverStep?.detail || null,
    state: serverStep ? uiStepState(serverStep.state || serverStep.status)
      : stageState === 'complete' || selectedRound < roundCount ? 'complete'
      : stageState === 'waiting' ? 'waiting'
        : index < currentStep ? 'complete' : index === currentStep ? getCurrentStepState(corner) : 'waiting',
  }; });
  const completeCount = steps.filter((step) => step.state === 'complete').length;

  return (
    <div className="workflow-tab">
      <div className="modal-stage-rail" role="tablist" aria-label="Corner stages">
        {STAGES.map((stage, index) => {
          const complete = index < activeStageIndex || corner.state === CORNER_STATES.DONE;
          const active = index === activeStageIndex && corner.state !== CORNER_STATES.DONE;
          const selected = stage.key === selectedStage;
          return <button type="button" role="tab" aria-selected={selected} key={stage.key} onClick={() => onStageChange(stage.key)} className={`${selected ? 'is-selected' : ''} ${active ? 'is-active' : ''} ${complete ? 'is-complete' : ''}`}><span>{complete ? <CheckCircleFilled /> : index + 1}</span><div><small>{stage.name}</small><strong>{stage.description}</strong></div>{active && <Tag color="processing">Current</Tag>}<RightOutlined /></button>;
        })}
      </div>

      <div className="stage-execution-header">
        <div><Text type="secondary">ACTIVE PATH</Text><Title level={4}>{STAGES[selectedStageIndex]?.name} execution #001</Title></div>
        <div className="stage-execution-progress"><span>{stageState === 'complete' ? 'Stage complete' : stageState === 'waiting' ? 'Stage not started' : `Step ${Math.min(currentStep + 1, STEP_DEFINITIONS.length)} of ${STEP_DEFINITIONS.length}`}</span><Progress percent={Math.round((completeCount / STEP_DEFINITIONS.length) * 100)} showInfo={false} size="small" /></div>
      </div>

      <div className="round-selector">
        <div><strong>Simulation groups</strong><Text type="secondary">Derived from algorithm call order</Text></div>
        <Segmented value={selectedRound} onChange={setSelectedRound} options={Array.from({ length: roundCount }, (_, index) => ({ value: index + 1, label: `Round ${index + 1}` }))} />
        <Tag color={selectedRound < roundCount || stageState === 'complete' ? 'success' : 'processing'}>Algorithm call #{selectedRound}</Tag>
      </div>

      <div className="stage-step-flow" aria-label={`${STAGES[selectedStageIndex]?.name}, Round ${selectedRound} progress`}>
        {steps.map((step, index) => <StageStep key={step.key} step={step} index={index} corner={corner} />)}
      </div>

      <div className="decision-panel">
        <div><span className="decision-icon"><BarChartOutlined /></span><div><small>ALGORITHM DECISION</small><strong>{getDecision(corner, stageState)}</strong><Text type="secondary">The decision is recorded with its complete input, output, reason, and timestamp.</Text></div></div>
        <div className="decision-paths"><span><SyncOutlined /> Add another simulation group</span><span>Advance to an algorithm-selected Stage <RightOutlined /></span><span><CheckCircleFilled /> Complete workflow</span></div>
      </div>
    </div>
  );
}

function StageStep({ step, index, corner }) {
  const icon = step.state === 'complete' ? <CheckCircleFilled />
    : step.state === 'running' ? <LoadingOutlined spin />
      : step.state === 'error' ? <WarningOutlined />
        : step.icon;
  const detail = step.serverDetail || getStepDetail(step.key, corner);
  return (
    <div className={`stage-step is-${step.state}`}>
      <div className="stage-step-index">{index + 1}</div>
      <div className="stage-step-icon">{icon}</div>
      <div className="stage-step-copy"><strong>{step.label}</strong><span>{step.description}</span></div>
      <div className="stage-step-detail">{detail}</div>
      <span className="stage-step-state">{step.state === 'complete' ? 'Complete' : step.state === 'running' ? 'In progress' : step.state === 'error' ? 'Attention required' : step.state === 'skipped' ? 'Skipped' : 'Waiting'}</span>
    </div>
  );
}

function InfoTab({ corner, run }) {
  const initialCode = corner.initialCode;
  const phaseRows = ['0', '90', '180', '270'].map((phase, index) => ({ key: phase, phase: `${phase}°`, vrefsel: initialCode.vrefsel[index], legsel: initialCode.legsel[index] }));
  return (
    <div className="modal-info-grid">
      <Card title="Run and input" size="small">
        <Descriptions column={2} size="small" items={[
          { key: 'run', label: 'Run', children: run.name },
          { key: 'algorithm', label: 'Algorithm plan', children: run.algorithm },
          { key: 'vdd', label: 'VDD', children: run.vdd },
          { key: 'vcm', label: 'VCM', children: run.vcm },
          { key: 'version', label: 'Pinned input version', children: corner.inputVersion },
          { key: 'stage', label: 'Active Stage', children: STAGES.find((stage) => stage.key === corner.stage)?.name },
          { key: 'sp', label: 'Original SP', span: 2, children: <Text className="mono">/baseline/{corner.id}.sp</Text> },
          { key: 'mt', label: 'Original MT', span: 2, children: <Text className="mono">/baseline/{corner.id}.mt</Text> },
        ]} />
      </Card>
      <Card title="Initial code" size="small" extra={<Tag color="blue">Parsed from MT</Tag>}>
        <div className="rangesel-value"><span>rangesel</span><strong>{initialCode.rangesel}</strong></div>
        <Table size="small" pagination={false} rowKey="key" dataSource={phaseRows} columns={[{ title: 'Phase', dataIndex: 'phase' }, { title: 'vrefsel', dataIndex: 'vrefsel', render: (value) => <Tag>{value}</Tag> }, { title: 'legsel', dataIndex: 'legsel', render: (value) => <Tag color="cyan">{value}</Tag> }]} />
      </Card>
      <Card title="Current execution" size="small" className="info-wide-card">
        <Descriptions column={3} size="small" items={[
          { key: 'path', label: 'Stage directory', span: 3, children: <Text className="mono">/runs/{run.id}/{corner.id}/{corner.stage}__001/</Text> },
          { key: 'round', label: 'Algorithm call', children: `#${corner.round}` },
          { key: 'cases', label: 'Current group', children: `${corner.testcaseDone} / ${corner.testcaseTotal} resolved` },
          { key: 'updated', label: 'Last update', children: corner.updatedAt },
        ]} />
      </Card>
    </div>
  );
}

function ResultsShell({ run, selectedStage, onStageChange, children }) {
  const stage = STAGES.find((item) => item.key === selectedStage);
  return <div className="results-tab"><div className="results-toolbar"><div><Text type="secondary">RESULT RENDERER</Text><strong>{run.algorithm} · {stage?.name}</strong></div><Segmented value={selectedStage} onChange={onStageChange} options={STAGES.map((item) => ({ value: item.key, label: item.name }))} /></div>{children}<Alert type="info" showIcon message="Renderer boundary" description="Each algorithm plan can provide a different chart or table for every Stage without changing the workflow UI." /></div>;
}

function AlgorithmAResults(props) {
  const { corner, selectedStage, selectedStageIndex } = props;
  const data = buildResultData(corner, selectedStageIndex);
  const option = selectedStage === 'stage2' ? buildBarOption(data) : buildLineOption(data, selectedStage === 'stage3');
  return <ResultsShell {...props}><div className="result-summary-row"><Card size="small"><Text type="secondary">Best testcase</Text><strong>tc_{String(3 + corner.number % 7).padStart(3, '0')}</strong></Card><Card size="small"><Text type="secondary">Minimum total loss</Text><strong>{data.best.toFixed(4)}</strong></Card><Card size="small"><Text type="secondary">Valid results</Text><strong>{Math.max(corner.testcaseDone, 8)}</strong></Card><Card size="small"><Text type="secondary">Algorithm outcome</Text><strong>{selectedStage === 'stage3' ? 'Final code selected' : 'Continue search'}</strong></Card></div><Card title={selectedStage === 'stage2' ? 'Loss comparison by candidate' : 'Four-phase loss trend'} size="small"><ReactECharts option={option} style={{ height: 360 }} opts={{ renderer: 'svg' }} /></Card></ResultsShell>;
}

function AlgorithmBResults(props) {
  const data = buildResultData(props.corner, props.selectedStageIndex);
  return <ResultsShell {...props}><Card title="Candidate ranking" size="small"><Table size="small" pagination={false} dataSource={data.labels.slice(0, 8).map((label, index) => ({ key: label, rank: index + 1, testcase: label, loss0: data.series[0][index], loss90: data.series[1][index], worst: Math.max(...data.series.map((series) => series[index])).toFixed(4) }))} columns={[{ title: 'Rank', dataIndex: 'rank' }, { title: 'Testcase', dataIndex: 'testcase' }, { title: 'Loss 0°', dataIndex: 'loss0' }, { title: 'Loss 90°', dataIndex: 'loss90' }, { title: 'Worst phase', dataIndex: 'worst' }]} /></Card></ResultsShell>;
}

function TestcasesTab({ corner, selectedStage, onStageChange }) {
  const { message } = App.useApp();
  const rows = useMemo(() => getTestcases(corner), [corner]);
  const stageRows = rows.filter((row) => row.stageKey === selectedStage);
  const availableStages = STAGES.filter((stage) => rows.some((row) => row.stageKey === stage.key));
  const copyPath = async (path) => {
    try { await navigator.clipboard.writeText(path); message.success('Testcase path copied'); }
    catch { message.error('Unable to copy the path'); }
  };
  const columns = [
    { title: 'Call', dataIndex: 'call', width: 70, render: (value) => `#${value}` },
    { title: 'Testcase', dataIndex: 'testcase', width: 110, render: (value) => <Text strong className="mono">{value}</Text> },
    { title: 'Status', dataIndex: 'status', width: 120, render: (value) => <StateTag state={value} /> },
    { title: 'Attempt', dataIndex: 'attempt', width: 80, render: (value) => `#${value}` },
    { title: 'LSF Job', dataIndex: 'job', width: 125, className: 'mono' },
    { title: 'Loss 0°', dataIndex: 'loss0', width: 90 },
    { title: 'Loss 90°', dataIndex: 'loss90', width: 90 },
    { title: 'Loss 180°', dataIndex: 'loss180', width: 95 },
    { title: 'Loss 270°', dataIndex: 'loss270', width: 95 },
    { title: 'Path', dataIndex: 'path', width: 76, fixed: 'right', render: (path) => <Tooltip title={path}><Button icon={<CopyOutlined />} aria-label={`Copy testcase path ${path}`} onClick={() => copyPath(path)} /></Tooltip> },
  ];
  return <div className="testcases-view">
    <div className="testcase-stage-tabs" role="tablist" aria-label="Testcase stages">
      {availableStages.map((stage) => {
        const count = rows.filter((row) => row.stageKey === stage.key).length;
        return <button type="button" role="tab" aria-selected={selectedStage === stage.key} className={selectedStage === stage.key ? 'is-selected' : ''} key={stage.key} onClick={() => onStageChange(stage.key)}><span>{stage.name}</span><small>{count}</small></button>;
      })}
    </div>
    <div className="testcases-tab"><div className="testcases-summary"><div><Text strong>{STAGES.find((stage) => stage.key === selectedStage)?.name}</Text><Text type="secondary">{STAGES.find((stage) => stage.key === selectedStage)?.description}</Text></div><Space><Tag color="success">{stageRows.filter((row) => row.status === CORNER_STATES.DONE).length} with results</Tag><Tag>{stageRows.length} total</Tag></Space></div><Table rowKey="key" size="small" sticky scroll={{ x: 1100, y: 440 }} pagination={false} dataSource={stageRows} columns={columns} /></div>
  </div>;
}

function getCurrentStep(corner, stageState) {
  if (stageState === 'waiting') return 0;
  if (stageState === 'complete' || corner.state === CORNER_STATES.DONE) return 6;
  return ({ not_started: 0, preparing: 1, queued: 3, running: 3, waiting_user: 4, status_unknown: 4, failed: 4, postprocess: 5 })[corner.state] ?? 0;
}

function getCurrentStepState(corner) {
  return [CORNER_STATES.FAILED, CORNER_STATES.STATUS_UNKNOWN, CORNER_STATES.WAITING_USER].includes(corner.state) ? 'error' : 'running';
}

function getDecision(corner, stageState) {
  if (stageState === 'waiting') return 'Waiting for the previous Stage decision';
  if (stageState === 'complete' || corner.state === CORNER_STATES.DONE) return 'Stage complete — decision recorded';
  if (corner.state === CORNER_STATES.WAITING_USER) return 'Blocked until unresolved Testcases are handled';
  if (corner.state === CORNER_STATES.FAILED || corner.state === CORNER_STATES.STATUS_UNKNOWN) return 'No decision — execution evidence is incomplete';
  return 'Waiting for the current simulation group';
}

function getStepDetail(key, corner) {
  const total = corner.testcaseTotal || 9;
  const details = {
    generate: `${total} complete parameter sets`,
    prepare: `${total} fixed Case directories`,
    submit: total === 1 ? '1 normal Job' : `Array Job [1-${total}]`,
    simulate: `${corner.testcaseDone} resolved · ${Math.max(total - corner.testcaseDone, 0)} open`,
    resolve: corner.issue || 'No unresolved exceptions',
    postprocess: '4 phase Loss values per valid Testcase',
    decide: `Uses all valid results in ${STAGES.find((stage) => stage.key === corner.stage)?.name}`,
  };
  return details[key];
}

function buildResultData(corner, stageIndex) {
  const count = 10 + stageIndex * 2;
  const labels = Array.from({ length: count }, (_, index) => `tc_${String(index + 1).padStart(3, '0')}`);
  const series = Array.from({ length: 4 }, (_, phase) => labels.map((_, index) => +(0.18 + ((corner.number * 13 + stageIndex * 17 + phase * 11 + index * 7) % 37) / 100 + Math.abs(index - (4 + stageIndex)) * 0.018).toFixed(4)));
  return { labels, series, best: Math.min(...series.flat()) };
}

function buildLineOption(data, finalStage) {
  const colors = ['#2563eb', '#16a34a', '#d97706', '#dc2626'];
  return { animation: false, tooltip: { trigger: 'axis' }, legend: { bottom: 0, data: ['Loss 0°', 'Loss 90°', 'Loss 180°', 'Loss 270°'] }, grid: { top: 28, right: 28, bottom: 58, left: 58 }, xAxis: { type: 'category', name: 'Testcase', data: data.labels, axisLabel: { rotate: data.labels.length > 10 ? 25 : 0 } }, yAxis: { type: 'value', name: 'Loss', splitLine: { lineStyle: { color: '#e8edf5' } } }, series: data.series.map((values, index) => ({ name: `Loss ${[0, 90, 180, 270][index]}°`, type: 'line', data: values, symbolSize: 6, lineStyle: { width: finalStage ? 2.5 : 1.8 }, itemStyle: { color: colors[index] } })) };
}

function buildBarOption(data) {
  const colors = ['#2563eb', '#16a34a', '#d97706', '#dc2626'];
  return { animation: false, tooltip: { trigger: 'axis' }, legend: { bottom: 0 }, grid: { top: 28, right: 28, bottom: 58, left: 58 }, xAxis: { type: 'category', data: data.labels }, yAxis: { type: 'value', name: 'Loss', splitLine: { lineStyle: { color: '#e8edf5' } } }, series: data.series.map((values, index) => ({ name: `Loss ${[0, 90, 180, 270][index]}°`, type: 'bar', data: values, itemStyle: { color: colors[index], borderRadius: [2, 2, 0, 0] } })) };
}

function getTestcases(corner) {
  const activeIndex = STAGES.findIndex((stage) => stage.key === corner.stage);
  return STAGES.flatMap((stage, stageIndex) => {
    const count = 5 + stageIndex * 2;
    if (stageIndex > activeIndex && corner.state !== CORNER_STATES.DONE) return [];
    return Array.from({ length: count }, (_, index) => {
      const hasResult = stageIndex < activeIndex || corner.state === CORNER_STATES.DONE || index < Math.min(corner.testcaseDone, count);
      const base = 0.2 + ((corner.number + stageIndex * 13 + index * 7) % 29) / 100;
      return {
        key: `${stage.key}-${index}`,
        stageKey: stage.key,
        stage: stage.name,
        call: stageIndex === activeIndex ? corner.round : Math.max(1, stageIndex + 1),
        testcase: `tc_${String(index + 1).padStart(3, '0')}`,
        status: hasResult ? CORNER_STATES.DONE : corner.state,
        attempt: index % 5 === 0 ? 2 : 1,
        job: `${812400 + corner.number + stageIndex}[${index + 1}]`,
        loss0: hasResult ? base.toFixed(4) : '—',
        loss90: hasResult ? (base + 0.031).toFixed(4) : '—',
        loss180: hasResult ? (base + 0.052).toFixed(4) : '—',
        loss270: hasResult ? (base + 0.019).toFixed(4) : '—',
        path: `/runs/${corner.id}/${stage.key}__001/tc_${String(index + 1).padStart(3, '0')}`,
      };
    });
  });
}
