import { useState } from 'react';
import { Button, Input, Segmented, Space, Tag, Tooltip, App as AntApp, Divider, Select } from 'antd';
import {
  FolderOpenOutlined, PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined,
  ExperimentOutlined, CloudUploadOutlined, BarChartOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useApp, selectActiveVcm } from '../store/appStore';
import { STAGE_KEYS } from '../data/mockData';

const PIPELINE_STAGES = [
  { value: STAGE_KEYS.STAGE1, label: 'Stage 1 · Param Scan' },
  { value: STAGE_KEYS.STAGE2, label: 'Stage 2 · Ref Sweep' },
  { value: STAGE_KEYS.STAGE3, label: 'Stage 3 · Iter Tune' },
  { value: STAGE_KEYS.FINAL, label: 'Stage 4 · Validation' },
];

export default function ProjectBar() {
  const { state, dispatch } = useApp();
  const { message } = AntApp.useApp();
  const vcm = selectActiveVcm(state);
  const isProduction = state.project.mode === 'Production';
  const [targetStage, setTargetStage] = useState(STAGE_KEYS.STAGE1);

  const toast = (label) => {
    const stageLabel = PIPELINE_STAGES.find((s) => s.value === targetStage).label;
    message.info(`${label} — ${stageLabel}`);
  };

  const run = () => {
    dispatch({ type: 'SET_RUN_STATE', value: 'running' });
    message.success(`Run started on ${vcm.name}`);
  };

  const togglePause = () => {
    if (state.runState === 'running') {
      dispatch({ type: 'SET_RUN_STATE', value: 'paused' });
      message.warning('Run paused');
    } else {
      dispatch({ type: 'SET_RUN_STATE', value: 'running' });
      message.info('Run resumed');
    }
  };

  return (
    <div className="toolbar">
      <Space size={4} wrap align="center" style={{ width: '100%' }}>
        <Tag color="blue" style={{ fontWeight: 600, fontSize: 12, marginInlineStart: 0 }}>Flow Calibration</Tag>

        <span style={{ fontSize: 11, opacity: 0.45, marginLeft: 8 }}>Root</span>
        <Input size="small" style={{ width: 160 }} value={state.project.root} prefix={<FolderOpenOutlined />}
          onChange={(e) => dispatch({ type: 'SET_PROJECT_ROOT', value: e.target.value })} />
        <span style={{ fontSize: 11, opacity: 0.45 }}>Output</span>
        <Input size="small" style={{ width: 140 }} value={state.project.output} prefix={<FolderOpenOutlined />}
          onChange={(e) => dispatch({ type: 'SET_OUTPUT_DIR', value: e.target.value })} />

        <Segmented size="small" value={state.project.mode} onChange={(v) => dispatch({ type: 'SET_MODE', value: v })}
          options={['Debug', 'Production']} />
        {isProduction && <Tag color="warning" style={{ fontSize: 11 }}>Production</Tag>}

        <Divider type="vertical" />

        <Tooltip title="Auto-advance all nodes">
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={run} disabled={state.runState === 'running'} size="small">Run</Button>
        </Tooltip>
        <Tooltip title={state.runState === 'running' ? 'Pause' : 'Resume'}>
          <Button icon={state.runState === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={togglePause} size="small" disabled={state.runState === 'idle'}>
            {state.runState === 'running' ? 'Pause' : 'Resume'}
          </Button>
        </Tooltip>

        <Divider type="vertical" />

        <span style={{ fontSize: 11, opacity: 0.45 }}>Target:</span>
        <Select size="small" style={{ width: 180 }} value={targetStage} onChange={setTargetStage}
          options={PIPELINE_STAGES} />

        <Button size="small" icon={<ExperimentOutlined />} onClick={() => toast('Generate Cases')}>Generate</Button>
        <Button size="small" icon={<CloudUploadOutlined />} onClick={() => toast('Submit to Queue')}>Submit</Button>
        <Button size="small" icon={<ThunderboltOutlined />} onClick={() => toast('Post-Process')}>Post-Proc</Button>
        <Button size="small" type="primary" ghost icon={<BarChartOutlined />} onClick={() => toast('Analyze')}>Analyze</Button>

        <Divider type="vertical" />

        <Tooltip title="Refresh status">
          <Button size="small" icon={<ReloadOutlined />} onClick={() => message.info('Refresh')}>Refresh</Button>
        </Tooltip>
      </Space>
    </div>
  );
}
