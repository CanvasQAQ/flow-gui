import { useMemo, useState } from 'react';
import { Modal, Tabs, Tag, Space, Typography, Descriptions, Table, Alert, Collapse, Card, Button, App as AntApp, Empty, Segmented, Tooltip } from 'antd';
import {
  BarChartOutlined, CheckCircleFilled, ClockCircleOutlined, CloudUploadOutlined,
  CopyOutlined, DownOutlined, FileTextOutlined, LoadingOutlined, PlayCircleOutlined,
  PlusOutlined, RedoOutlined, RightOutlined, SyncOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useApp, selectActiveVcm } from '../store/appStore';
import { STATE_META, STAGE_LABELS, STAGE_KEYS, CORNER_STATES } from '../data/mockData';
import { Stage1Chart, Stage2Chart } from './StageEvidenceCharts';

const PIPELINE_STEPS = [
  { stage: STAGE_KEYS.STAGE1, label: 'Stage 1', sub: 'Param Scan' },
  { stage: STAGE_KEYS.STAGE2, label: 'Stage 2', sub: 'Ref Sweep' },
  { stage: STAGE_KEYS.STAGE3, label: 'Stage 3', sub: 'Iter Tune' },
  { stage: STAGE_KEYS.FINAL, label: 'Stage 4', sub: 'Validation' },
];

export default function CornerDetailModal({ cornerId, onClose }) {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);
  const corner = useMemo(() => vcm.corners.find((c) => c.id === cornerId), [vcm, cornerId]);
  if (!corner) return null;

  const meta = STATE_META[corner.state];

  return (
    <Modal
      open={!!cornerId} onCancel={onClose} width={1120} footer={null} destroyOnHidden
      className="corner-detail-modal"
      style={{ top: 20 }}
      styles={{ body: { maxHeight: 'calc(100vh - 126px)', overflowY: 'auto', padding: '8px 16px 16px' } }}
      title={
        <Space size={8}>
          <span>{corner.id}</span>
          <Tag color={meta.color}>{meta.label}</Tag>
          <Tag>Batch {corner.batchId}</Tag>
          <Tag>Iter {corner.iteration}</Tag>
        </Space>
      }
    >
      <Tabs size="small" defaultActiveKey="pipeline"
        items={[
          { key: 'pipeline', label: 'Pipeline', children: <PipelineTab corner={corner} /> },
          { key: 'info', label: 'Info', children: <InfoTab corner={corner} /> },
          { key: 'evidence', label: 'Evidence', children: <EvidenceTab corner={corner} /> },
          { key: 'batch', label: 'Cases', children: <BatchTab corner={corner} /> },
          { key: 'raw', label: 'Raw', children: <RawTab corner={corner} /> },
        ]}
      />
    </Modal>
  );
}

function PipelineTab({ corner }) {
  const { message } = AntApp.useApp();
  const stageIdx = corner.stage ? PIPELINE_STEPS.findIndex((s) => s.stage === corner.stage) : -1;
  const initialStage = corner.stage || STAGE_KEYS.STAGE1;
  const [expandedStage, setExpandedStage] = useState(initialStage);
  const expandedIdx = PIPELINE_STEPS.findIndex((stage) => stage.stage === expandedStage);
  const snapshot = buildStageSnapshot(corner, expandedIdx, stageIdx);
  const activeSnapshot = buildStageSnapshot(corner, Math.max(0, stageIdx), stageIdx);
  const supportsRounds = expandedStage === STAGE_KEYS.STAGE1;

  const stepScope = supportsRounds ? ` · round ${snapshot.currentRound}` : '';
  const runStep = (step) => message.info(`Run ${step} · ${corner.id} · ${expandedStage}${stepScope}`);
  const rewindStep = (step) => message.warning(`Rewind to ${step} · ${corner.id} · ${expandedStage}${stepScope}`);

  return (
    <div className="corner-pipeline" aria-label="Corner pipeline">
      <div className="corner-stage-rail" role="tablist" aria-label="Pipeline stages">
        {PIPELINE_STEPS.map((stage, index) => {
          const isExpanded = stage.stage === expandedStage;
          const isComplete = corner.state === CORNER_STATES.DONE || index < stageIdx;
          const isCurrent = index === stageIdx && corner.state !== CORNER_STATES.DONE;
          return (
            <button
              type="button"
              role="tab"
              aria-selected={isExpanded}
              aria-controls="corner-stage-workspace"
              className={`corner-stage-tab ${isExpanded ? 'is-expanded' : ''} ${isComplete ? 'is-complete' : ''} ${isCurrent ? 'is-current' : ''}`}
              key={stage.stage}
              onClick={() => setExpandedStage(stage.stage)}
            >
              <span className="corner-stage-number">{isComplete ? <CheckCircleFilled /> : index + 1}</span>
              <span className="corner-stage-name"><small>{stage.label}</small><strong>{stage.sub}</strong></span>
              {index === stageIdx + 1 && <span className="corner-stage-next">Next</span>}
              {isExpanded ? <DownOutlined className="corner-stage-chevron" /> : <RightOutlined className="corner-stage-chevron" />}
            </button>
          );
        })}
      </div>

      <section id="corner-stage-workspace" className="corner-stage-workspace" role="tabpanel">
        <div className="corner-workspace-heading">
          <div>
            <span className="corner-workspace-eyebrow">{PIPELINE_STEPS[expandedIdx].label}</span>
            <Typography.Title level={5}>{PIPELINE_STEPS[expandedIdx].sub}</Typography.Title>
          </div>
          <Typography.Text type="secondary">Click a step to run or rewind from that point</Typography.Text>
        </div>

        {supportsRounds && (
          <>
            <div className="corner-rounds" aria-label="Stage 1 rounds">
              {snapshot.rounds.map((round) => (
                <button type="button" className={`corner-round ${round.state === 'running' ? 'is-running' : ''}`} key={round.id}>
                  <span>Round {round.id}</span>
                  <StateLabel state={round.state} label={round.state === 'complete' ? 'Complete' : round.state === 'running' ? 'Running' : 'Pending'} />
                </button>
              ))}
              <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => message.info(`Create a new Stage 1 round for ${corner.id}`)}>New round</Button>
            </div>
            <div className="corner-loop-hint"><SyncOutlined /> Analyze can continue with a new Stage 1 round or advance to Stage 2</div>
          </>
        )}

        <div className="corner-process-list">
          <ProcessRow
            icon={<FileTextOutlined />}
            index={1}
            label="Generate testcase"
            status={<StateLabel state={snapshot.generate.state} label={snapshot.generate.label} />}
            detail={`${snapshot.generate.done} / ${snapshot.total} generated`}
            onRun={() => runStep('Generate testcase')}
            onRewind={() => rewindStep('Generate testcase')}
          />
          <ProcessRow
            icon={<CloudUploadOutlined />}
            index={2}
            label="Submit to LSF"
            status={<StateLabel state={snapshot.submit.state} label={snapshot.submit.label} />}
            detail={`${snapshot.submit.done} jobs`}
            onRun={() => runStep('Submit to LSF')}
            onRewind={() => rewindStep('Submit to LSF')}
          />

          <div className="corner-process-row corner-execution-row">
            <div className="corner-process-identity">
              <span className="corner-process-icon"><PlayCircleOutlined /></span>
              <span className="corner-process-index">3</span>
              <strong>Execution</strong>
            </div>
            <div className="corner-execution-lanes">
              <ExecutionLane
                label="LSF simulation"
                segments={[
                  { value: snapshot.sim.finished, className: 'is-complete' },
                  { value: snapshot.sim.running, className: 'is-running' },
                  { value: snapshot.sim.queued, className: 'is-waiting' },
                ]}
                total={snapshot.total}
                stats={[
                  { state: 'complete', text: `${snapshot.sim.finished} finished` },
                  { state: 'running', text: `${snapshot.sim.running} running` },
                  { state: 'waiting', text: `${snapshot.sim.queued} queued` },
                ]}
                onRun={() => runStep('LSF simulation')}
                onRewind={() => rewindStep('LSF simulation')}
              />
              <div className="corner-lane-dependency"><DownOutlined /> Post-process becomes ready per testcase as soon as its result is available</div>
              <ExecutionLane
                label="Post-process"
                emphasized
                segments={[
                  { value: snapshot.post.complete, className: 'is-complete' },
                  { value: snapshot.total - snapshot.post.complete, className: snapshot.post.running ? 'is-running-soft' : 'is-waiting' },
                ]}
                total={snapshot.total}
                stats={[
                  { state: 'complete', text: `${snapshot.post.complete} complete` },
                  ...(snapshot.post.running ? [{ state: 'running', text: 'Running' }] : []),
                ]}
                onRun={() => runStep('Post-process')}
                onRewind={() => rewindStep('Post-process')}
              />
            </div>
          </div>

          <ProcessRow
            icon={<BarChartOutlined />}
            index={4}
            label="Analyze & decide"
            status={<StateLabel state={snapshot.analysis.state} label={snapshot.analysis.label} />}
            detail={snapshot.analysis.detail}
            onRun={() => runStep('Analyze & decide')}
            onRewind={() => rewindStep('Analyze & decide')}
          />
        </div>

        <div className="corner-decision-paths" aria-label="Possible analysis decisions">
          {supportsRounds && <span><RedoOutlined /> Continue with next round</span>}
          {expandedIdx < PIPELINE_STEPS.length - 1 && <span>Advance to {PIPELINE_STEPS[expandedIdx + 1].label} <RightOutlined /></span>}
        </div>
      </section>

      <div className="corner-pipeline-summary" role="status">
        {corner.state === CORNER_STATES.RUNNING ? <LoadingOutlined spin />
          : corner.state === CORNER_STATES.DONE ? <CheckCircleFilled />
          : corner.state === CORNER_STATES.FAILED ? <WarningOutlined />
          : <ClockCircleOutlined />}
        <strong>{STATE_META[corner.state].label}</strong>
        <span>{PIPELINE_STEPS[Math.max(0, stageIdx)]?.label || 'Stage 1'}</span>
        {stageIdx === 0 && <span>Round {activeSnapshot.currentRound}</span>}
        <Typography.Text type="secondary">
          {activeSnapshot.sim.finished} of {activeSnapshot.total} simulations finished · {activeSnapshot.post.complete} post-processed
        </Typography.Text>
      </div>
    </div>
  );
}

function ProcessRow({ icon, index, label, status, detail, onRun, onRewind }) {
  return (
    <div className="corner-process-row" tabIndex={0}>
      <div className="corner-process-identity">
        <span className="corner-process-icon">{icon}</span>
        <span className="corner-process-index">{index}</span>
        <strong>{label}</strong>
      </div>
      <div className="corner-process-status">{status}</div>
      <span className="corner-process-detail">{detail}</span>
      <StepActions onRun={onRun} onRewind={onRewind} label={label} />
    </div>
  );
}

function ExecutionLane({ label, segments, total, stats, emphasized = false, onRun, onRewind }) {
  return (
    <div className={`corner-execution-lane ${emphasized ? 'is-emphasized' : ''}`} tabIndex={0}>
      <strong>{label}</strong>
      <div className="corner-segmented-progress" aria-label={`${label} progress`}>
        {segments.map((segment, index) => (
          <span key={`${segment.className}-${index}`} className={segment.className} style={{ flexGrow: segment.value, flexBasis: `${(segment.value / total) * 100}%` }} />
        ))}
      </div>
      <div className="corner-lane-stats">
        {stats.map((stat) => <StateLabel key={stat.text} state={stat.state} label={stat.text} compact />)}
      </div>
      <StepActions onRun={onRun} onRewind={onRewind} label={label} />
    </div>
  );
}

function StepActions({ onRun, onRewind, label }) {
  return (
    <div className="corner-step-actions">
      <Button type="text" size="small" icon={<PlayCircleOutlined />} aria-label={`Run from ${label}`} onClick={onRun}>Run from here</Button>
      <Button type="text" size="small" danger icon={<RedoOutlined />} aria-label={`Rewind to ${label}`} onClick={onRewind}>Rewind to here</Button>
    </div>
  );
}

function StateLabel({ state, label, compact = false }) {
  const icon = state === 'complete' || state === 'submitted' ? <CheckCircleFilled />
    : state === 'running' ? <LoadingOutlined spin />
    : state === 'ready' ? <span className="corner-ready-dot" />
    : <ClockCircleOutlined />;
  return <span className={`corner-state-label state-${state} ${compact ? 'is-compact' : ''}`}>{icon}{label}</span>;
}

function buildStageSnapshot(corner, selectedStageIdx, activeStageIdx) {
  const total = 32;
  const isDone = corner.state === CORNER_STATES.DONE || selectedStageIdx < activeStageIdx;
  const isActive = selectedStageIdx === activeStageIdx && ![CORNER_STATES.NOT_STARTED, CORNER_STATES.NO_BASELINE].includes(corner.state);
  const isFuture = selectedStageIdx > activeStageIdx || activeStageIdx < 0;
  const supportsRounds = selectedStageIdx === 0;
  const currentRound = supportsRounds && selectedStageIdx === activeStageIdx ? Math.max(1, corner.iteration || 1) : 1;
  const rounds = supportsRounds ? Array.from({ length: currentRound }, (_, index) => ({
    id: index + 1,
    state: index + 1 < currentRound || isDone ? 'complete' : 'running',
  })) : [];

  if (isDone) {
    return {
      total, currentRound, rounds,
      generate: { state: 'complete', label: 'Complete', done: total },
      submit: { state: 'complete', label: 'Submitted', done: total },
      sim: { finished: total, running: 0, queued: 0 },
      post: { complete: total, running: false },
      analysis: { state: 'complete', label: 'Complete', detail: 'Decision recorded' },
    };
  }

  if (isFuture || !isActive) {
    return {
      total, currentRound, rounds: supportsRounds ? [{ id: 1, state: 'waiting' }] : [],
      generate: { state: 'waiting', label: 'Pending', done: 0 },
      submit: { state: 'waiting', label: 'Pending', done: 0 },
      sim: { finished: 0, running: 0, queued: total },
      post: { complete: 0, running: false },
      analysis: { state: 'waiting', label: 'Waiting', detail: 'Waiting for stage to start' },
    };
  }

  const finished = Math.min(total, 14 + (corner.index % 9));
  const running = Math.min(total - finished, 6 + (corner.index % 5));
  const queued = total - finished - running;
  const postComplete = Math.max(0, finished - 9);
  return {
    total, currentRound, rounds,
    generate: { state: 'complete', label: 'Complete', done: total },
    submit: { state: 'submitted', label: 'Submitted', done: total },
    sim: { finished, running, queued },
    post: { complete: postComplete, running: corner.state === CORNER_STATES.RUNNING && postComplete < finished },
    analysis: {
      state: corner.state === CORNER_STATES.WAITING_USER ? 'ready' : 'waiting',
      label: corner.state === CORNER_STATES.WAITING_USER ? 'Ready' : 'Waiting',
      detail: corner.state === CORNER_STATES.WAITING_USER ? 'Required post-process results are ready' : 'Runs when required post-process results are ready',
    },
  };
}

function InfoTab({ corner }) {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Card size="small" title="Basic Info" styles={{ body: { padding: '8px 12px' } }}>
        <Descriptions size="small" column={2} colon={false}>
          <Descriptions.Item label="ID">{corner.id}</Descriptions.Item>
          <Descriptions.Item label="VCM">{corner.vcmId}</Descriptions.Item>
          <Descriptions.Item label="Batch">{corner.batchId}</Descriptions.Item>
          <Descriptions.Item label="Case">{corner.caseId}</Descriptions.Item>
          <Descriptions.Item label="Iteration">{corner.iteration}</Descriptions.Item>
          <Descriptions.Item label="Index">{corner.index}</Descriptions.Item>
          <Descriptions.Item label="Updated">{new Date(corner.updatedAt).toLocaleString()}</Descriptions.Item>
          <Descriptions.Item label="Created">{new Date(corner.updatedAt - 86400000).toLocaleString()}</Descriptions.Item>
        </Descriptions>
      </Card>

      {corner.candidate && (
        <Card size="small" title="Current Candidate" styles={{ body: { padding: '8px 12px' } }}>
          <Descriptions size="small" column={3} colon={false}>
            <Descriptions.Item label="code_x"><Tag color="blue">{corner.candidate.code_x}</Tag></Descriptions.Item>
            <Descriptions.Item label="metric_sum">{corner.candidate.metric_sum}</Descriptions.Item>
            <Descriptions.Item label="metric_max">{corner.candidate.metric_max}</Descriptions.Item>
          </Descriptions>
          <Table
            size="small" rowKey="phase" pagination={false} style={{ marginTop: 8 }}
            dataSource={['A','B','C','D'].map((p,i) => ({
              key:p, phase: p,
              ref_sel: corner.candidate.ref_sel[i],
              code_y: corner.candidate.code_y[i],
              metric: corner.candidate.metric[i],
            }))}
            columns={[
              { title:'Phase', dataIndex:'phase', width:80 },
              { title:'ref_sel', dataIndex:'ref_sel', width:80, render:(v)=><Tag>{v}</Tag> },
              { title:'code_y', dataIndex:'code_y', width:80, render:(v)=><Tag color="cyan">{v}</Tag> },
              { title:'metric', dataIndex:'metric' },
            ]}
          />
        </Card>
      )}

      <Card size="small" title="Planner" styles={{ body: { padding: '8px 12px' } }}>
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space size={4}>
            <Tag color={corner.planner.current_stage_complete ? 'green' : 'blue'}>
              {corner.planner.current_stage_complete ? 'Stage complete' : 'In progress'}
            </Tag>
            <Tag color="purple">Next: {corner.planner.next_stage ? STAGE_LABELS[corner.planner.next_stage] : '-'}</Tag>
          </Space>
          <Alert type={corner.state==='failed'?'error':corner.state==='waiting_user'?'warning':'info'} showIcon message={corner.planner.reason} style={{fontSize:12}} />
        </Space>
      </Card>
    </Space>
  );
}

function EvidenceTab({ corner }) {
  return (
    <Collapse size="small" defaultActiveKey={['stage1','stage2']}
      items={[
        { key:'stage1', label:<Space size={4}>Stage 1 · Parameter Scan</Space>,
          children: corner.stage1 ? <Stage1Chart data={corner.stage1} /> : <Alert type="info" message="Not executed" /> },
        { key:'stage2', label:<Space size={4}>Stage 2 · Reference Sweep</Space>,
          children: corner.stage2 ? <Stage2Chart data={corner.stage2} /> : <Alert type="info" message="Not executed" /> },
        { key:'stage3', label:<Space size={4}>Stage 3 · Iterative Tuning</Space>,
          children: corner.stage3 ? (
            <Table size="small" rowKey="round" pagination={false} dataSource={corner.stage3}
              columns={[
                { title:'Round',dataIndex:'round',width:60 },
                { title:'Active',dataIndex:'active',width:80 },
                { title:'Best Code',dataIndex:'best_code',width:80 },
                { title:'Metric',dataIndex:'best_metric' },
                { title:'Limit Hit',dataIndex:'limit_hit',width:80,render:(v)=>v?<Tag color="orange">yes</Tag>:<Tag>no</Tag> },
                { title:'Saturated',dataIndex:'saturated',width:80,render:(v)=>v?<Tag color="red">YES</Tag>:<Tag>no</Tag> },
              ]} />
          ) : <Alert type="info" message="Not executed" /> },
        { key:'stage4', label:<Space size={4}>Stage 4 · Validation</Space>,
          children: corner.stage4 ? (
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="Testcases">{corner.stage4.testcases}</Descriptions.Item>
              <Descriptions.Item label="Metric Sum">{corner.stage4.metric_sum}</Descriptions.Item>
              <Descriptions.Item label="Metric Max">{corner.stage4.metric_max}</Descriptions.Item>
              <Descriptions.Item label="Result">{corner.stage4.passed?<Tag color="green">PASSED</Tag>:<Tag color="red">FAILED</Tag>}</Descriptions.Item>
              <Descriptions.Item label="Per Phase" span={2}>
                <Space wrap size={4}>{corner.stage4.phases.map((p)=><Tag key={p.phase} color="blue">{p.phase}={p.metric}</Tag>)}</Space>
              </Descriptions.Item>
            </Descriptions>
          ) : <Alert type="info" message="Not run" /> },
      ]} />
  );
}

function BatchTab({ corner }) {
  const { message } = AntApp.useApp();
  const [selectedStage, setSelectedStage] = useState(corner.stage || STAGE_KEYS.STAGE1);
  const selectedStageIdx = PIPELINE_STEPS.findIndex((stage) => stage.stage === selectedStage);
  const activeStageIdx = corner.stage ? PIPELINE_STEPS.findIndex((stage) => stage.stage === corner.stage) : -1;
  const stageHasData = {
    [STAGE_KEYS.STAGE1]: !!corner.stage1,
    [STAGE_KEYS.STAGE2]: !!corner.stage2,
    [STAGE_KEYS.STAGE3]: !!corner.stage3,
    [STAGE_KEYS.FINAL]: !!corner.stage4,
  }[selectedStage];

  const cases = useMemo(() => {
    if (!stageHasData) return [];
    const list = [];
    const stageOffset = (selectedStageIdx + 1) * 97;
    const selectedComplete = corner.state === CORNER_STATES.DONE || selectedStageIdx < activeStageIdx;
    const readyCount = selectedComplete ? 12 : corner.state === CORNER_STATES.FAILED ? 8 : 9;
    for (let i = 0; i < 12; i += 1) {
      const isReady = i < readyCount;
      const metric = (phase) => isReady
        ? +(((corner.index + 1) * (i + 3) * (phase + 5) + stageOffset) % 400 / 1000 + 0.2).toFixed(3)
        : null;
      list.push({
        caseId: `${selectedStage}_case_${String(i + 1).padStart(2, '0')}`,
        code: `0x${(i * 17 + corner.iteration + stageOffset).toString(16).padStart(4, '0')}`,
        ready: isReady,
        a_metric: metric(0),
        b_metric: metric(1),
        c_metric: metric(2),
        d_metric: metric(3),
        case_dir: `/projects/calibration/output/${corner.id}/${corner.batchId}/${selectedStage}/case_${i + 1}`,
      });
    }
    return list;
  }, [activeStageIdx, corner, selectedStage, selectedStageIdx, stageHasData]);

  const copyPath = async (path) => {
    try {
      await navigator.clipboard.writeText(path);
      message.success('Path copied');
    } catch {
      message.error('Unable to copy path');
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12} className="corner-cases-tab">
      <div className="corner-cases-toolbar">
        <div>
          <Typography.Text strong>Stage cases</Typography.Text>
          <Typography.Text type="secondary">Switch stages to inspect their testcase inputs and results.</Typography.Text>
        </div>
        <Segmented
          aria-label="Select stage cases"
          value={selectedStage}
          onChange={setSelectedStage}
          options={PIPELINE_STEPS.map((stage) => ({ label: stage.label, value: stage.stage }))}
        />
      </div>
      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="Batch">{corner.batchId}</Descriptions.Item>
        <Descriptions.Item label="Stage">{STAGE_LABELS[selectedStage]}</Descriptions.Item>
        <Descriptions.Item label={selectedStage === STAGE_KEYS.STAGE1 ? 'Round' : 'Execution'}>
          {selectedStage === STAGE_KEYS.STAGE1 ? Math.max(1, corner.iteration || 1) : 'Single pass'}
        </Descriptions.Item>
        <Descriptions.Item label="Cases">{cases.length}</Descriptions.Item>
        <Descriptions.Item label="With result">{cases.filter((c) => c.ready).length}</Descriptions.Item>
        <Descriptions.Item label="Pending result">{cases.filter((c) => !c.ready).length}</Descriptions.Item>
      </Descriptions>
      <Card size="small" title={`${PIPELINE_STEPS[selectedStageIdx].label} results`} styles={{ body: { padding: 8 } }}>
        <Table
          size="small" rowKey="caseId" pagination={false}
          dataSource={cases}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="This stage has not generated cases yet" /> }}
          columns={[
            { title: 'Case', dataIndex: 'caseId', width: 130 },
            { title: 'Code', dataIndex: 'code', width: 80 },
            { title: 'Phase A', dataIndex: 'a_metric', width: 70 },
            { title: 'Phase B', dataIndex: 'b_metric', width: 70 },
            { title: 'Phase C', dataIndex: 'c_metric', width: 70 },
            { title: 'Phase D', dataIndex: 'd_metric', width: 70 },
            { title: 'Result', dataIndex: 'ready', width: 85, render: (v) => v ? <Tag color="green" style={{ fontSize: 10 }}>available</Tag> : <Tag style={{ fontSize: 10 }}>pending</Tag> },
            {
              title: 'Path', dataIndex: 'case_dir', width: 58, align: 'center',
              render: (path, row) => (
                <Tooltip title="Copy path">
                  <Button type="text" size="small" icon={<CopyOutlined />} aria-label={`Copy path for ${row.caseId}`} onClick={() => copyPath(path)} />
                </Tooltip>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}

function RawTab({ corner }) {
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <Card size="small" title="Planner JSON" style={{ flex: 1 }} styles={{ body: { padding: 8 } }}>
        <pre className="raw-code">{JSON.stringify(corner.planner, null, 2)}</pre>
      </Card>
      <Card size="small" title="Backend Log" style={{ flex: 1 }} styles={{ body: { padding: 8 } }}>
        <pre className="raw-code">{`[mock] ${corner.id} batch=${corner.batchId} stage=${corner.stage||'-'} state=${corner.state}
planner.reason: ${corner.planner.reason}
last_action: ${corner.lastAction}
next_step: ${corner.nextStep}
error: ${corner.error||'none'}`}</pre>
      </Card>
    </div>
  );
}
