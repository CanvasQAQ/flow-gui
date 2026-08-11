import { useMemo } from 'react';
import { Modal, Tabs, Tag, Space, Typography, Descriptions, Table, Alert, Collapse, Card, Row, Col, Button, App as AntApp, Steps } from 'antd';
import {
  ExperimentOutlined, CloudUploadOutlined, ThunderboltOutlined, BarChartOutlined,
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

const SUBSTEPS = ['Generate', 'Submit', 'Post-Process', 'Analyze'];

export default function CornerDetailModal({ cornerId, onClose }) {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);
  const corner = useMemo(() => vcm.corners.find((c) => c.id === cornerId), [vcm, cornerId]);
  if (!corner) return null;

  const meta = STATE_META[corner.state];

  return (
    <Modal
      open={!!cornerId} onCancel={onClose} width={960} footer={null} destroyOnClose
      style={{ top: 24 }}
      styles={{ body: { maxHeight: 'calc(100vh - 140px)', overflowY: 'auto', padding: '12px 16px' } }}
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
  const meta = STATE_META[corner.state];
  const stageIdx = corner.stage ? PIPELINE_STEPS.findIndex((s) => s.stage === corner.stage) : -1;
  const substepIdx = corner.state === CORNER_STATES.NOT_STARTED || corner.state === CORNER_STATES.NO_BASELINE ? -1
    : corner.state === CORNER_STATES.RUNNING ? (corner.stage === STAGE_KEYS.STAGE1 ? 1 : 2)
    : corner.state === CORNER_STATES.WAITING_USER || corner.state === CORNER_STATES.FAILED ? 1
    : corner.state === CORNER_STATES.DONE ? SUBSTEPS.length : 0;
  const currentSubstep = substepIdx >= 0 && substepIdx < SUBSTEPS.length ? SUBSTEPS[substepIdx] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card size="small" title="Stage Progress" styles={{ body: { padding: '12px 16px' } }}>
        <Steps
          size="small"
          current={Math.max(0, stageIdx)}
          status={corner.state === CORNER_STATES.FAILED ? 'error'
            : corner.state === CORNER_STATES.WAITING_USER ? 'process'
            : corner.state === CORNER_STATES.DONE ? 'finish'
            : 'process'}
          items={PIPELINE_STEPS.map((s, i) => ({
            title: s.label,
            description: i === stageIdx
              ? (corner.state === CORNER_STATES.FAILED ? 'Failed'
                : corner.state === CORNER_STATES.DONE ? 'Complete'
                : corner.state === CORNER_STATES.WAITING_USER ? 'Needs review'
                : currentSubstep ? `Step: ${currentSubstep}` : 'Idle')
              : i < stageIdx ? 'Complete' : 'Pending',
          }))}
        />
      </Card>

      <Row gutter={12}>
        <Col span={12}>
          <Card size="small" title="Current Status" styles={{ body: { padding: '8px 12px' } }}>
            <Space direction="vertical" size={4}>
              <Space>
                <Tag color={meta.color}>{meta.label}</Tag>
                {corner.stage && <Tag>{STAGE_LABELS[corner.stage]}</Tag>}
              </Space>
              <Typography.Text style={{ fontSize: 12 }}>
                {corner.state === CORNER_STATES.NOT_STARTED && 'Waiting for initialization'}
                {corner.state === CORNER_STATES.NO_BASELINE && 'No baseline data available'}
                {corner.state === CORNER_STATES.RUNNING && `${corner.lastAction}`}
                {corner.state === CORNER_STATES.WAITING_USER && 'Awaiting manual review'}
                {corner.state === CORNER_STATES.FAILED && corner.error}
                {corner.state === CORNER_STATES.DONE && 'All stages complete'}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                Next: {corner.nextStep || '-'}
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="Actions" styles={{ body: { padding: '8px 12px' } }}>
            <Space direction="vertical" size={4}>
              <Space size={4} wrap>
                <Button size="small" icon={<ExperimentOutlined />} onClick={() => message.info(`Generate for ${corner.id}`)}>Generate</Button>
                <Button size="small" icon={<CloudUploadOutlined />} onClick={() => message.info(`Submit ${corner.id}`)}>Submit</Button>
                <Button size="small" icon={<ThunderboltOutlined />} onClick={() => message.info(`Post-Process ${corner.id}`)}>Post-Proc</Button>
                <Button size="small" icon={<BarChartOutlined />} onClick={() => message.info(`Analyze ${corner.id}`)}>Analyze</Button>
              </Space>
              {corner.state === CORNER_STATES.WAITING_USER && (
                <Alert type="warning" showIcon message="Manual review required" style={{ fontSize: 11 }} />
              )}
              {corner.state === CORNER_STATES.FAILED && (
                <Alert type="error" showIcon message={corner.error} style={{ fontSize: 11 }} />
              )}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
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
  const cases = useMemo(() => {
    const list = [];
    for (let i = 0; i < 12; i += 1) {
      list.push({
        caseId: `${corner.caseId}_${i + 1}`,
        code: `0x${(i * 17 + corner.iteration).toString(16).padStart(4, '0')}`,
        ready: i < 9,
        a_metric: i < 9 ? +(0.2 + Math.random() * 0.4).toFixed(3) : null,
        b_metric: i < 9 ? +(0.2 + Math.random() * 0.4).toFixed(3) : null,
        c_metric: i < 9 ? +(0.2 + Math.random() * 0.4).toFixed(3) : null,
        d_metric: i < 9 ? +(0.2 + Math.random() * 0.4).toFixed(3) : null,
        case_dir: `/projects/calibration/output/${corner.id}/${corner.batchId}/case_${i + 1}`,
      });
    }
    return list;
  }, [corner]);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="Batch">{corner.batchId}</Descriptions.Item>
        <Descriptions.Item label="Stage">{corner.stage ? STAGE_LABELS[corner.stage] : '-'}</Descriptions.Item>
        <Descriptions.Item label="Iteration">{corner.iteration}</Descriptions.Item>
        <Descriptions.Item label="Cases">{cases.length}</Descriptions.Item>
        <Descriptions.Item label="Ready">{cases.filter((c) => c.ready).length}</Descriptions.Item>
        <Descriptions.Item label="Pending">{cases.filter((c) => !c.ready).length}</Descriptions.Item>
      </Descriptions>
      <Card size="small" title="Per-Phase Metric" styles={{ body: { padding: 8 } }}>
        <Table
          size="small" rowKey="caseId" pagination={false}
          dataSource={cases}
          columns={[
            { title: 'Case', dataIndex: 'caseId', width: 110 },
            { title: 'Code', dataIndex: 'code', width: 80 },
            { title: 'Phase A', dataIndex: 'a_metric', width: 70 },
            { title: 'Phase B', dataIndex: 'b_metric', width: 70 },
            { title: 'Phase C', dataIndex: 'c_metric', width: 70 },
            { title: 'Phase D', dataIndex: 'd_metric', width: 70 },
            { title: 'Status', dataIndex: 'ready', width: 70, render: (v) => v ? <Tag color="green" style={{ fontSize: 10 }}>ready</Tag> : <Tag color="orange" style={{ fontSize: 10 }}>pending</Tag> },
            { title: 'Dir', dataIndex: 'case_dir', ellipsis: true, render: (v) => <Typography.Text code style={{ fontSize: 10 }}>{v}</Typography.Text> },
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
