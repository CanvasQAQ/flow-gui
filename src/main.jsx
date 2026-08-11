import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, App as AntApp } from 'antd';
import './index.css';
import { AppProvider, useApp } from './store/appStore';
import MainView from './components/MainView';

function TickSimulator() {
  const { state, dispatch } = useApp();
  useEffect(() => {
    if (state.runState !== 'running') return undefined;
    const t = setInterval(() => {
      dispatch({ type: 'ADVANCE_CORNERS', scope: 'all' });
    }, 1500);
    return () => clearInterval(t);
  }, [state.runState, dispatch]);
  return null;
}

function Root() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 4,
          fontSize: 13,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        },
      }}
    >
      <AntApp>
        <AppProvider>
          <TickSimulator />
          <MainView />
        </AppProvider>
      </AntApp>
    </ConfigProvider>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
