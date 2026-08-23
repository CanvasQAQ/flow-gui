import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, App as AntApp } from 'antd';
import './index.css';
import { AppProvider, useApp } from './store/appStore';
import MainView from './components/MainView';

function ThemedApp() {
  const { state } = useApp();
  return (
    <ConfigProvider
      wave={{ disabled: !state.animationsEnabled }}
      theme={{
        token: {
          colorPrimary: '#3459d6',
          borderRadius: 8,
          fontSize: 14,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          motion: state.animationsEnabled,
        },
      }}
    >
      <AntApp>
        <MainView />
      </AntApp>
    </ConfigProvider>
  );
}

function Root() {
  return <AppProvider><ThemedApp /></AppProvider>;
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
