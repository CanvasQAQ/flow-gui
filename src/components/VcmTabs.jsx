import { Tabs, Tag, Space } from 'antd';
import { CaretRightOutlined } from '@ant-design/icons';
import { useApp, summarize } from '../store/appStore';

export default function VcmTabs() {
  const { state, dispatch } = useApp();

  const items = state.vcms.map((vcm) => {
    const s = summarize(vcm.corners);
    return {
      key: String(vcm.id),
      label: (
        <Space size={6}>
          <span style={{ fontWeight: 600 }}>{vcm.name}</span>
          {s.running > 0 && (
            <Tag color="processing" style={{ margin: 0 }}>
              <CaretRightOutlined /> {s.running}
            </Tag>
          )}
          <Tag style={{ margin: 0, fontSize: 11 }}>{s.done}/{s.total}</Tag>
        </Space>
      ),
      children: null,
    };
  });

  return (
    <Tabs
      activeKey={String(state.activeVcmId)}
      onChange={(k) => dispatch({ type: 'SET_ACTIVE_VCM', id: Number(k) })}
      items={items}
      size="small"
      tabBarStyle={{ marginBottom: 0 }}
    />
  );
}
