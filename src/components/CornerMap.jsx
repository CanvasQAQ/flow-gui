import { memo, useCallback, useMemo, useRef, useState } from 'react';
import { Space, Typography } from 'antd';
import { useApp, selectActiveVcm } from '../store/appStore';
import { CORNER_STATES, STATE_META, STAGE_LABELS } from '../data/mockData';

const CELL = 14;
const GAP = 3;
const COLS = 52;
const PADDING = 16;

const STATE_COLORS = {
  [CORNER_STATES.NOT_STARTED]: '#ebedf0',
  [CORNER_STATES.NO_BASELINE]: '#afb8c1',
  [CORNER_STATES.RUNNING]: '#0969da',
  [CORNER_STATES.WAITING_USER]: '#bf8700',
  [CORNER_STATES.FAILED]: '#cf222e',
  [CORNER_STATES.DONE]: '#1a7f37',
};

const Cell = memo(function Cell({ x, y, color, cornerId, onClick, onHover }) {
  return (
    <rect
      x={x} y={y} width={CELL} height={CELL} rx={2} ry={2}
      fill={color}
      onMouseEnter={(e) => onHover(e, cornerId)}
      onMouseLeave={() => onHover(null, null)}
      onClick={() => onClick(cornerId)}
      style={{ cursor: 'pointer', transition: 'fill 100ms ease, stroke 100ms ease' }}
    />
  );
});

export default function CornerMap({ onPick }) {
  const { state } = useApp();
  const vcm = selectActiveVcm(state);
  const svgRef = useRef(null);
  const containerRef = useRef(null);
  const [hover, setHover] = useState(null);

  const corners = vcm.corners;
  const rows = Math.ceil(corners.length / COLS);
  const svgWidth = COLS * (CELL + GAP) + GAP + PADDING * 2;
  const svgHeight = rows * (CELL + GAP) + GAP + PADDING * 2;

  const cells = useMemo(() => {
    const list = [];
    for (let i = 0; i < corners.length; i += 1) {
      const c = corners[i];
      const col = i % COLS;
      const row = Math.floor(i / COLS);
      list.push({
        key: c.id, corner: c,
        x: PADDING + col * (CELL + GAP) + GAP,
        y: PADDING + row * (CELL + GAP) + GAP,
      });
    }
    return list;
  }, [corners]);

  const handleCellHover = useCallback((e, id) => {
    if (!e || !id) { setHover(null); return; }
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHover({ id, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const handleCellClick = useCallback(
    (id) => {
      const c = corners.find((x) => x.id === id);
      if (c) onPick?.(c);
    },
    [corners, onPick],
  );

  const hoverCorner = hover ? corners.find((c) => c.id === hover.id) : null;
  const meta = hoverCorner ? STATE_META[hoverCorner.state] : null;

  return (
    <div className="corner-map" ref={containerRef} style={{ position: 'relative' }}>
      <div className="map-header">
        <Space size={4}>
          <Typography.Text strong style={{ fontSize: 12 }}>{vcm.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {corners.length} nodes
          </Typography.Text>
        </Space>
        <div style={{ display: 'flex', gap: 12 }}>
          {Object.values(CORNER_STATES).map((s) => (
            <Space key={s} size={4}>
              <span className="legend-swatch" style={{ background: STATE_COLORS[s] }} />
              <Typography.Text style={{ fontSize: 10, opacity: 0.55 }}>{STATE_META[s].label}</Typography.Text>
            </Space>
          ))}
        </div>
      </div>

      <div className="map-body">
        <svg ref={svgRef} width={svgWidth} height={svgHeight} style={{ display: 'block', shapeRendering: 'crispEdges' }}>
          <rect x={0} y={0} width={svgWidth} height={svgHeight} rx={4} fill="transparent" />
          {cells.map(({ key, corner, x, y }) => (
            <Cell key={key} x={x} y={y} cornerId={key}
              color={STATE_COLORS[corner.state]}
              onClick={handleCellClick} onHover={handleCellHover} />
          ))}
        </svg>
      </div>

      {hover && hoverCorner && meta && (
        <div className="map-tooltip" style={{
          left: Math.min(hover.x + 14, (containerRef.current?.clientWidth || 800) - 230),
          top: hover.y + 18,
        }}>
          <div className="tooltip-id">{hoverCorner.id}</div>
          <div>
            <span className="tooltip-dot" style={{ background: STATE_COLORS[hoverCorner.state] }} />
            {meta.label}{hoverCorner.stage ? ` · ${STAGE_LABELS[hoverCorner.stage]}` : ''}
          </div>
          <div className="tooltip-meta">Batch {hoverCorner.batchId} · Case {hoverCorner.caseId} · Iter {hoverCorner.iteration}</div>
          <div style={{ marginTop: 3 }}>{hoverCorner.lastAction}</div>
          <div>Next: {hoverCorner.nextStep}</div>
          {hoverCorner.error && <div className="tooltip-error">{hoverCorner.error}</div>}
        </div>
      )}
    </div>
  );
}
