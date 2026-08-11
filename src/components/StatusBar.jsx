import { Space, Tag, Typography } from 'antd';
import { PlayCircleOutlined, PauseCircleOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { useApp, selectActiveVcm } from '../store/appStore';

export default function StatusBar() {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);

  const runIcon =
    state.runState === 'running' ? <PlayCircleOutlined />
    : state.runState === 'paused' ? <PauseCircleOutlined />
    : <MinusCircleOutlined />;

  const runLabel =
    state.runState === 'running' ? 'Running'
    : state.runState === 'paused' ? 'Paused'
    : 'Idle';

  const runColor =
    state.runState === 'running' ? 'green'
    : state.runState === 'paused' ? 'orange'
    : 'default';

  return (
    <Space size={8}>
      <Typography.Text style={{ fontSize: 11 }}>{vcm.name}</Typography.Text>
      <Tag color={runColor} style={{ fontSize: 11 }}>
        {runIcon} {runLabel}
      </Tag>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {vcm.corners.length} corners
      </Typography.Text>
    </Space>
  );
}
