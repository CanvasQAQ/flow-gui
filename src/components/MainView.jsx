import { useState } from 'react';
import { Layout } from 'antd';
import ProjectBar from './ProjectBar';
import VcmTabs from './VcmTabs';
import PipelineBar from './PipelineBar';
import CornerMap from './CornerMap';
import RunQueuePanel from './RunQueuePanel';
import CornerDetailModal from './CornerDetailModal';
import StatusBar from './StatusBar';

export default function MainView() {
  const [pickedCorner, setPickedCorner] = useState(null);

  return (
    <Layout className="app-shell">
      <Layout.Header className="app-header">
        <ProjectBar />
      </Layout.Header>
      <Layout.Content className="app-content">
        <VcmTabs />
        <PipelineBar />
        <div className="main-grid">
          <div className="map-panel">
            <CornerMap onPick={(c) => setPickedCorner(c.id)} />
          </div>
          <div className="issues-panel">
            <RunQueuePanel onPick={(c) => setPickedCorner(c.id)} />
          </div>
        </div>
      </Layout.Content>
      <Layout.Footer className="app-footer">
        <StatusBar />
      </Layout.Footer>
      <CornerDetailModal cornerId={pickedCorner} onClose={() => setPickedCorner(null)} />
    </Layout>
  );
}
