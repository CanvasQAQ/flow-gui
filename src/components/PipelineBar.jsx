import { Steps } from 'antd';
import { useApp, selectActiveVcm, summarize } from '../store/appStore';
import { STAGE_KEYS, CORNER_STATES } from '../data/mockData';

export default function PipelineBar() {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);
  const s = summarize(vcm.corners);

  const items = [
    { title: 'Stage 1', description: `${vcm.corners.filter((c) => c.stage === STAGE_KEYS.STAGE1 && c.state !== CORNER_STATES.DONE).length} nodes` },
    { title: 'Stage 2', description: `${vcm.corners.filter((c) => c.stage === STAGE_KEYS.STAGE2 && c.state !== CORNER_STATES.DONE).length} nodes` },
    { title: 'Stage 3', description: `${vcm.corners.filter((c) => c.stage === STAGE_KEYS.STAGE3 && c.state !== CORNER_STATES.DONE).length} nodes` },
    { title: 'Stage 4', description: `${vcm.corners.filter((c) => c.stage === STAGE_KEYS.FINAL || c.state === CORNER_STATES.DONE).length} nodes` },
  ];

  const activeStage = vcm.corners.some((c) => c.stage === STAGE_KEYS.STAGE1 && c.state !== CORNER_STATES.DONE) ? 0
    : vcm.corners.some((c) => c.stage === STAGE_KEYS.STAGE2 && c.state !== CORNER_STATES.DONE) ? 1
    : vcm.corners.some((c) => c.stage === STAGE_KEYS.STAGE3 && c.state !== CORNER_STATES.DONE) ? 2 : 3;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0' }}>
      <span style={{ fontSize: 11, opacity: 0.45 }}>Total {s.total}</span>
      <Steps size="small" current={activeStage} items={items} style={{ flex: 1 }} />
    </div>
  );
}
