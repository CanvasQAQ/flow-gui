import { useMemo } from 'react';
import { Card, Space, Tag, Typography, Empty } from 'antd';
import { AlertOutlined } from '@ant-design/icons';
import { useApp, selectActiveVcm } from '../store/appStore';
import { CORNER_STATES, STATE_META, STAGE_LABELS } from '../data/mockData';

export default function RunQueuePanel({ onPick }) {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);

  const issues = useMemo(() => {
    return vcm.corners.filter(
      (c) => c.state === CORNER_STATES.WAITING_USER || c.state === CORNER_STATES.FAILED
    );
  }, [vcm.corners]);

  return (
    <Card
      size="small"
      title={
        <Space>
          <AlertOutlined style={{ color: STATE_META[CORNER_STATES.WAITING_USER].color }} />
          <span>Open Issues</span>
          <Tag color="red">{issues.length}</Tag>
        </Space>
      }
      styles={{ body: { padding: 4 } }}
    >
      {issues.length === 0 ? (
        <Empty description="No open issues" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          {issues.map((c) => (
            <div
              key={c.id}
              className="issue-row"
              style={{ borderLeftColor: STATE_META[c.state].color }}
              onClick={() => onPick?.(c)}
            >
              <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
                <Typography.Text strong style={{ fontSize: 11 }}>{c.id}</Typography.Text>
                <Tag color={STATE_META[c.state].color} style={{ fontSize: 10, margin: 0 }}>
                  {STATE_META[c.state].label}
                </Tag>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 10, display: 'block', marginTop: 2 }}>
                {c.stage ? STAGE_LABELS[c.stage] : '-'} · {c.batchId}
              </Typography.Text>
              {c.error && (
                <Typography.Text style={{ fontSize: 10, color: '#cf222e', display: 'block', marginTop: 2 }}>
                  {c.error}
                </Typography.Text>
              )}
            </div>
          ))}
        </Space>
      )}
    </Card>
  );
}
