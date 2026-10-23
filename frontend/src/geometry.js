const EPS = 0.004;
const MIN_SIDE = 0.04;

export function round4(value) {
  return Math.round(Number(value) * 10000) / 10000;
}

export function clampShape(shape) {
  let w = Math.min(1, Math.max(MIN_SIDE, Number(shape.w) || MIN_SIDE));
  let h = Math.min(1, Math.max(MIN_SIDE, Number(shape.h) || MIN_SIDE));
  let x = Number(shape.x) || 0;
  let y = Number(shape.y) || 0;
  if (x < 0) x = 0;
  if (y < 0) y = 0;
  if (x + w > 1) x = 1 - w;
  if (y + h > 1) y = 1 - h;
  if (x < 0) {
    x = 0;
    w = 1;
  }
  if (y < 0) {
    y = 0;
    h = 1;
  }
  return { ...shape, x, y, w, h };
}

function rectPoly(shape) {
  const x2 = shape.x + shape.w;
  const y2 = shape.y + shape.h;
  return [
    [shape.x, shape.y],
    [x2, shape.y],
    [x2, y2],
    [shape.x, y2],
  ];
}

function ellipsePoly(shape, steps = 24) {
  const rx = shape.w / 2;
  const ry = shape.h / 2;
  const cx = shape.x + rx;
  const cy = shape.y + ry;
  const points = [];
  for (let i = 0; i < steps; i += 1) {
    const t = (i / steps) * Math.PI * 2;
    points.push([cx + Math.cos(t) * rx, cy + Math.sin(t) * ry]);
  }
  return points;
}

function polyOf(shape) {
  return shape.kind === 'circle' ? ellipsePoly(shape) : rectPoly(shape);
}

function project(poly, axis) {
  let min = Infinity;
  let max = -Infinity;
  for (const point of poly) {
    const d = point[0] * axis[0] + point[1] * axis[1];
    if (d < min) min = d;
    if (d > max) max = d;
  }
  return [min, max];
}

function separated(polyA, polyB) {
  for (let i = 0; i < polyA.length; i += 1) {
    const p = polyA[i];
    const q = polyA[(i + 1) % polyA.length];
    const ex = q[0] - p[0];
    const ey = q[1] - p[1];
    const len = Math.hypot(ex, ey) || 1;
    const axis = [-ey / len, ex / len];
    const [a0, a1] = project(polyA, axis);
    const [b0, b1] = project(polyB, axis);
    if (a1 <= b0 + EPS || b1 <= a0 + EPS) return true;
  }
  return false;
}

export function overlaps(a, b) {
  if (!a || !b) return false;
  const pa = polyOf(a);
  const pb = polyOf(b);
  return !separated(pa, pb) && !separated(pb, pa);
}

export function hitsAny(shape, others) {
  return others.some((other) => other.id !== shape.id && overlaps(shape, other));
}

export function moveShape(origin, dx, dy) {
  return clampShape({ ...origin, x: origin.x + dx, y: origin.y + dy });
}

export function resizeShape(origin, handle, dx, dy) {
  let { x, y, w, h } = origin;
  if (handle.includes('e')) w += dx;
  if (handle.includes('s')) h += dy;
  if (handle.includes('w')) {
    x += dx;
    w -= dx;
  }
  if (handle.includes('n')) {
    y += dy;
    h -= dy;
  }
  if (w < MIN_SIDE) {
    if (handle.includes('w')) x -= MIN_SIDE - w;
    w = MIN_SIDE;
  }
  if (h < MIN_SIDE) {
    if (handle.includes('n')) y -= MIN_SIDE - h;
    h = MIN_SIDE;
  }
  return clampShape({ ...origin, x, y, w, h });
}

export function geometryBody(shape, { size = true } = {}) {
  const body = { x: round4(shape.x), y: round4(shape.y) };
  if (size) {
    body.w = round4(shape.w);
    body.h = round4(shape.h);
  }
  return body;
}

export function findOpening(existing, kind, aspect = 1) {
  const w = 0.3;
  const rawH = kind === 'circle' ? w * (aspect || 1) : 0.24;
  const h = Math.min(0.9, Math.max(MIN_SIDE, rawH));
  const spots = [
    [0.35, 0.38],
    [0.08, 0.08],
    [0.6, 0.08],
    [0.08, 0.6],
    [0.55, 0.55],
    [0.22, 0.42],
    [0.4, 0.12],
    [0.12, 0.32],
  ];
  for (const [x, y] of spots) {
    const shape = clampShape({ id: '__draft__', kind, x, y, w, h });
    if (!hitsAny(shape, existing)) {
      const { id, ...rest } = shape;
      return rest;
    }
  }
  return null;
}

export function isTopLevel(shape) {
  return shape.parent_id == null || shape.parent_id === '';
}
