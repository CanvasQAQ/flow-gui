import ReactECharts from 'echarts-for-react';

const COLORS = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f'];
const PHASE_LABELS = ['A', 'B', 'C', 'D'];

export function Stage1Chart({ data }) {
  if (!data || !data.sels) return null;

  const sels = data.sels;
  const xData = sels.map((s) => s.idx).reverse();
  const bestX = data.bestIdx;
  const stopX = data.stopIdx;

  const series = PHASE_LABELS.map((label, i) => ({
    name: `Phase ${label}`,
    type: 'line',
    smooth: false,
    symbol: 'circle',
    symbolSize: 4,
    lineStyle: { width: 1.5 },
    itemStyle: { color: COLORS[i] },
    data: [...sels].reverse().map((s) => s.sa[i]),
  }));

  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e8e8e8',
      textStyle: { color: '#1f1f1f', fontSize: 11 },
    },
    legend: {
      data: PHASE_LABELS.map((l) => `Phase ${l}`),
      bottom: 0,
      textStyle: { fontSize: 10 },
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: { top: 8, right: 16, bottom: 30, left: 48 },
    xAxis: {
      type: 'category',
      data: xData,
      axisLine: { lineStyle: { color: '#d9d9d9' } },
      axisTick: { show: false },
      axisLabel: { fontSize: 9, color: '#8c8c8c', interval: 3 },
      name: 'code_x',
      nameLocation: 'middle',
      nameGap: 22,
      nameTextStyle: { fontSize: 10, color: '#8c8c8c' },
    },
    yAxis: {
      type: 'value',
      name: 'metric',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 9, color: '#8c8c8c' },
      nameTextStyle: { fontSize: 10, color: '#8c8c8c' },
    },
    series,
    ...(bestX != null ? {
      markLine: {
        animation: false,
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', color: '#52c41a', width: 1 },
        label: { formatter: `Best idx=${bestX}`, fontSize: 9, color: '#52c41a' },
        data: [{ xAxis: bestX }],
      },
    } : {}),
    ...(stopX != null ? {
      markArea: {
        animation: false,
        silent: true,
        data: [[{ xAxis: stopX, itemStyle: { color: 'rgba(255,77,79,0.06)' } }, { xAxis: xData[0] }]],
        label: { show: true, position: 'insideTop', formatter: `Stop \u2192 Stage 2`, fontSize: 9, color: '#ff4d4f' },
      },
    } : {}),
  };

  return (
    <div>
      <div style={{ fontSize: 11, color: '#8c8c8c', padding: '4px 0 0 8px' }}>
        Stage 1 Parameter Scan · Scan downward, groups of 5
        {stopX != null && ` · Stopped at idx ${stopX} (metric increased)`}
        {bestX != null && ` · Best idx ${bestX}`}
      </div>
      <ReactECharts option={option} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

export function Stage2Chart({ data }) {
  if (!data || !data.items) return null;

  const xData = data.items.map((it) => it.code);

  const series = PHASE_LABELS.map((label, i) => ({
    name: `Phase ${label}`,
    type: 'line',
    smooth: false,
    symbol: 'circle',
    symbolSize: 4,
    lineStyle: { width: 1.5 },
    itemStyle: { color: COLORS[i] },
    data: data.items.map((it) => it.sa[i]),
  }));

  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e8e8e8',
      textStyle: { color: '#1f1f1f', fontSize: 11 },
    },
    legend: {
      data: PHASE_LABELS.map((l) => `Phase ${l}`),
      bottom: 0,
      textStyle: { fontSize: 10 },
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: { top: 8, right: 16, bottom: 30, left: 48 },
    xAxis: {
      type: 'category',
      data: xData,
      axisLine: { lineStyle: { color: '#d9d9d9' } },
      axisTick: { show: false },
      axisLabel: { fontSize: 9, color: '#8c8c8c' },
      name: 'code_y',
      nameLocation: 'middle',
      nameGap: 22,
      nameTextStyle: { fontSize: 10, color: '#8c8c8c' },
    },
    yAxis: {
      type: 'value',
      name: 'metric',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 9, color: '#8c8c8c' },
      nameTextStyle: { fontSize: 10, color: '#8c8c8c' },
    },
    series,
  };

  return (
    <div>
      <div style={{ fontSize: 11, color: '#8c8c8c', padding: '4px 0 0 8px' }}>
        Stage 2 Reference Sweep · code_x = {data.code_x} · Best code = {data.best_code} (metric={data.best_metric})
        {data.items.some((it) => it.limit_hit) && ' · Limit hit detected'}
      </div>
      <ReactECharts option={option} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}
