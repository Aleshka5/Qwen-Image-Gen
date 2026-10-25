import { useEffect, useRef, useState } from 'react';
import { api, asList, asObject, shapeSource } from '../api';
import {
  findOpening,
  geometryBody,
  hitsAny,
  isTopLevel,
  moveShape,
  resizeShape,
} from '../geometry';
import { cx, shapeColor, withAlpha, withConfig } from '../model';
import { useWorkspace } from '../workspace';
import ContextMenu from './ContextMenu';
import { TrashIcon } from './Icons';

export default function StageEditor({ projectId, parentId, visible }) {
  const {
    config,
    projects,
    notify,
    push,
    openObject,
    variantEpoch,
  } = useWorkspace();
  const cfg = withConfig(config);
  const nested = Boolean(parentId);
  const project = projects.find((item) => item.id === projectId);

  const viewportRef = useRef(null);
  const frameRef = useRef(null);
  const panRef = useRef({ x: 0, y: 0 });
  const zoomRef = useRef(1);
  const naturalRef = useRef({ w: 0, h: 0 });
  const clickTimer = useRef(null);
  const shapesRef = useRef([]);
  const busyRef = useRef('');

  const [shapes, setShapes] = useState([]);
  const [variantUrls, setVariantUrls] = useState({});
  const [fit, setFit] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 40, y: 40 });
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [draftBad, setDraftBad] = useState(false);
  const [sealed, setSealed] = useState(false);
  const [busy, setBusy] = useState('');
  busyRef.current = busy;
  const [error, setError] = useState('');
  const [menu, setMenu] = useState(null);
  const [imageBroken, setImageBroken] = useState(false);
  const [photoUrl, setPhotoUrl] = useState('');

  const imageUrl = nested ? shapeSource(parentId, false) : (photoUrl || project?.preview_url || '');
  shapesRef.current = shapes;
  panRef.current = pan;
  zoomRef.current = zoom;

  useEffect(() => () => clearTimeout(clickTimer.current), []);

  useEffect(() => {
    let stop = false;
    setImageBroken(false);
    setMenu(null);
    api.project(projectId)
      .then((data) => {
        if (stop) return;
        const all = asList(data, 'shapes');
        const list = all.filter((shape) => (nested ? shape.parent_id === parentId : isTopLevel(shape)));
        setShapes(list);
        if (list.some((shape) => shape.saved || shape.geometry_locked)) setSealed(true);
        const loaded = asObject(data, 'project');
        if (!nested && loaded?.preview_url) setPhotoUrl(loaded.preview_url);
      })
      .catch((err) => {
        if (!stop) setError(err.message);
      });
    return () => {
      stop = true;
    };
  }, [projectId, parentId, nested]);

  const variantSig = shapes.map((shape) => `${shape.id}:${shape.selected_variant_id || ''}`).join('|');

  useEffect(() => {
    let stop = false;
    const chosen = shapes.filter((shape) => shape.selected_variant_id);
    if (!chosen.length) {
      setVariantUrls({});
      return undefined;
    }
    Promise.all(chosen.map(async (shape) => {
      try {
        const data = await api.shapeVariants(shape.id);
        const list = asList(data, 'variants');
        const match = list.find((item) => item.selected) || list.find((item) => item.id === shape.selected_variant_id);
        return [shape.id, match?.url || ''];
      } catch {
        return [shape.id, ''];
      }
    })).then((pairs) => {
      if (!stop) setVariantUrls(Object.fromEntries(pairs));
    });
    return () => {
      stop = true;
    };
  }, [variantSig, variantEpoch]);

  function center() {
    const vp = viewportRef.current;
    const { w: nw, h: nh } = naturalRef.current;
    if (!vp || !nw || !nh) return;
    const bounds = vp.getBoundingClientRect();
    if (bounds.width < 8 || bounds.height < 8) return;
    const scale = Math.min(bounds.width / nw, bounds.height / nh) * 0.92;
    const w = nw * scale;
    const h = nh * scale;
    const nextPan = { x: (bounds.width - w) / 2, y: (bounds.height - h) / 2 };
    setFit({ w, h });
    setZoom(1);
    zoomRef.current = 1;
    setPan(nextPan);
    panRef.current = nextPan;
  }

  useEffect(() => {
    if (!visible) return undefined;
    const id = requestAnimationFrame(() => center());
    const onResize = () => center();
    window.addEventListener('resize', onResize);
    return () => {
      cancelAnimationFrame(id);
      window.removeEventListener('resize', onResize);
    };
  }, [visible, imageUrl]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return undefined;
    function onWheel(event) {
      event.preventDefault();
      const rect = vp.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const current = zoomRef.current;
      const next = Math.min(8, Math.max(0.25, current * (event.deltaY < 0 ? 1.08 : 1 / 1.08)));
      const k = next / current;
      const panNow = panRef.current;
      const nextPan = { x: px - (px - panNow.x) * k, y: py - (py - panNow.y) * k };
      zoomRef.current = next;
      panRef.current = nextPan;
      setZoom(next);
      setPan(nextPan);
    }
    vp.addEventListener('wheel', onWheel, { passive: false });
    return () => vp.removeEventListener('wheel', onWheel);
  }, [visible]);

  function showError(message) {
    setError(message);
    notify(message);
    window.setTimeout(() => {
      setError((current) => (current === message ? '' : current));
    }, 2400);
  }

  function zoomBy(factor) {
    const vp = viewportRef.current;
    if (!vp) return;
    const rect = vp.getBoundingClientRect();
    const px = rect.width / 2;
    const py = rect.height / 2;
    const current = zoomRef.current;
    const next = Math.min(8, Math.max(0.25, current * factor));
    const k = next / current;
    const panNow = panRef.current;
    const nextPan = { x: px - (px - panNow.x) * k, y: py - (py - panNow.y) * k };
    zoomRef.current = next;
    panRef.current = nextPan;
    setZoom(next);
    setPan(nextPan);
  }

  function onImageLoad(event) {
    naturalRef.current = { w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight };
    setImageBroken(false);
    center();
  }

  function aspect() {
    const { w, h } = naturalRef.current;
    if (!w || !h) return 1;
    return w / h;
  }

  async function addShape(kind) {
    if (sealed || busy) return;
    if (shapes.length >= cfg.max_shapes) {
      showError(`You can add up to ${cfg.max_shapes} shapes.`);
      return;
    }
    const opening = findOpening(shapes, kind, aspect());
    if (!opening) {
      showError('No open space for another shape.');
      return;
    }
    setBusy('add');
    try {
      const data = await api.createShape(projectId, {
        parent_id: parentId || null,
        kind,
        ...geometryBody(opening),
      });
      const created = asObject(data, 'shape');
      if (!created?.id) throw new Error('The shape was created without an id.');
      setShapes((list) => [...list, created]);
      setSelectedId(created.id);
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function useEntireFrame() {
    if (sealed || busy) return;
    if (shapes.length > 0) {
      showError('Use entire frame only when no other shapes are here.');
      return;
    }
    setBusy('frame');
    try {
      const data = await api.createShape(projectId, {
        parent_id: parentId || null,
        kind: 'full_frame',
        x: 0,
        y: 0,
        w: 1,
        h: 1,
      });
      const created = asObject(data, 'shape');
      if (!created?.id) throw new Error('The shape was created without an id.');
      setShapes([created]);
      setSelectedId(created.id);
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function confirmSave() {
    if (sealed || busy) return;
    if (!shapes.length) {
      showError('Add a shape before saving.');
      return;
    }
    setBusy('save');
    try {
      await api.commit(projectId, parentId || null);
      const data = await api.project(projectId);
      const all = asList(data, 'shapes');
      const list = all.filter((shape) => (nested ? shape.parent_id === parentId : isTopLevel(shape)));
      setShapes(list.length ? list.map((shape) => ({ ...shape, saved: shape.saved !== false })) : shapes.map((shape) => ({ ...shape, saved: true, geometry_locked: true })));
      setSealed(true);
      setSelectedId(null);
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function removeShape(shape) {
    if (busy) return;
    setBusy('delete');
    setMenu(null);
    try {
      await api.deleteShape(shape.id);
      setShapes((list) => list.filter((item) => item.id !== shape.id));
      setSelectedId(null);
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy('');
    }
  }

  function descend(shape) {
    clearTimeout(clickTimer.current);
    setMenu(null);
    if (!shape.saved) return;
    if (!nested) {
      push({ type: 'editor', projectId, parentId: shape.id });
      return;
    }
    const type = shape.type === 'text' ? 'text' : 'photo';
    push({ type, projectId, shapeId: shape.id });
  }

  function openMenu(shape, node) {
    const rect = node?.getBoundingClientRect();
    if (!rect || rect.width < 1 || rect.height < 1) return;
    setMenu({
      id: shape.id,
      x: rect.right,
      y: rect.top,
      anchor: 'top-right',
    });
  }

  function selectShape(shape, node) {
    setSelectedId(shape.id);
    if (!shape.saved) {
      setMenu(null);
      return;
    }
    clearTimeout(clickTimer.current);
    clickTimer.current = setTimeout(() => openMenu(shape, node), 260);
  }

  function onShapePointerDown(event, shape, handle) {
    if (event.button > 0 || busyRef.current === 'move' || busyRef.current === 'delete') return;
    event.stopPropagation();
    setSelectedId(shape.id);
    const shapeNode = event.currentTarget.closest('.shape');
    const frame = frameRef.current?.getBoundingClientRect();
    if (!frame?.width || !frame?.height) return;
    const origin = { ...shape };
    const start = { x: event.clientX, y: event.clientY };
    let moved = false;
    let latest = origin;
    const canResize = Boolean(handle) && !sealed && !shape.saved && !shape.geometry_locked && shape.kind !== 'full_frame';

    function move(ev) {
      const dx = (ev.clientX - start.x) / frame.width;
      const dy = (ev.clientY - start.y) / frame.height;
      if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 4) moved = true;
      latest = canResize ? resizeShape(origin, handle, dx, dy) : moveShape(origin, dx, dy);
      setDraft(latest);
      setDraftBad(hitsAny(latest, shapesRef.current));
    }

    function up() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!moved) {
        setDraft(null);
        setDraftBad(false);
        selectShape(shape, shapeNode);
        return;
      }
      if (hitsAny(latest, shapesRef.current)) {
        setDraft(null);
        setDraftBad(false);
        showError('That overlaps another shape.');
        return;
      }
      const sizeLocked = Boolean(shape.saved || shape.geometry_locked);
      busyRef.current = 'move';
      setBusy('move');
      api.updateShape(shape.id, geometryBody(latest, { size: !sizeLocked }))
        .then((data) => {
          const updated = asObject(data, 'shape') || {};
          setShapes((list) => list.map((item) => (
            item.id === shape.id ? { ...item, ...updated, ...geometryBody(latest, { size: !sizeLocked }) } : item
          )));
        })
        .catch((err) => showError(err.message))
        .finally(() => {
          setDraft(null);
          setDraftBad(false);
          busyRef.current = '';
          setBusy('');
        });
    }

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  function onBackgroundPointerDown(event) {
    if (event.button > 0) return;
    if (event.target.closest('.shape') || event.target.closest('button')) return;
    setSelectedId(null);
    setMenu(null);
    const start = { x: event.clientX, y: event.clientY, pan: { ...panRef.current } };
    let moved = false;
    function move(ev) {
      if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 3) moved = true;
      const next = { x: start.pan.x + (ev.clientX - start.x), y: start.pan.y + (ev.clientY - start.y) };
      panRef.current = next;
      setPan(next);
    }
    function up() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!moved) {
        setSelectedId(null);
        setMenu(null);
      }
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  async function switchType(shape) {
    const next = shape.type === 'text' ? 'photo' : 'text';
    setMenu(null);
    setBusy('type');
    try {
      const data = await api.setType(shape.id, next);
      const updated = asObject(data, 'shape') || {};
      setShapes((list) => list.map((item) => (
        item.id === shape.id ? { ...item, ...updated, type: updated.type || next } : item
      )));
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy('');
    }
  }

  const menuShape = shapes.find((shape) => shape.id === menu?.id);
  const shown = shapes.map((shape, index) => {
    const live = draft && draft.id === shape.id ? { ...shape, ...draft } : shape;
    return { ...live, color: shapeColor(shape, config, index, nested) };
  });

  return (
    <div className="view-frame editor">
      <div
        className="viewport"
        ref={viewportRef}
        onPointerDown={onBackgroundPointerDown}
      >
        <div
          className="world"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
        >
          <div className="frame" ref={frameRef} style={{ width: fit.w || 0, height: fit.h || 0 }}>
            {imageUrl ? (
              <img
                src={imageUrl}
                alt={nested ? 'Crop' : 'Photo'}
                draggable="false"
                onLoad={onImageLoad}
                onError={() => setImageBroken(true)}
              />
            ) : (
              <div className="frame-empty">This project has no photo yet.</div>
            )}
            {shown.map((shape) => {
              const selected = shape.id === selectedId;
              const canResize = selected && !sealed && !shape.saved && !shape.geometry_locked && shape.kind !== 'full_frame';
              const url = variantUrls[shape.id];
              return (
                <div
                  key={shape.id}
                  id={`shape-${shape.id}`}
                  className={cx(
                    'shape',
                    shape.kind === 'circle' && 'is-circle',
                    selected && 'is-selected',
                    shape.saved && 'is-saved',
                    draftBad && draft?.id === shape.id && 'is-bad',
                  )}
                  role={shape.saved ? 'button' : 'group'}
                  tabIndex={0}
                  aria-label={shapeLabel(shape)}
                  style={{
                    left: `${shape.x * 100}%`,
                    top: `${shape.y * 100}%`,
                    width: `${shape.w * 100}%`,
                    height: `${shape.h * 100}%`,
                    color: shape.color,
                    background: withAlpha(shape.color, url ? 0.18 : 0.34),
                    borderColor: shape.color,
                  }}
                  onPointerDown={(event) => onShapePointerDown(event, shape)}
                  onDoubleClick={(event) => {
                    event.stopPropagation();
                    descend(shape);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') descend(shape);
                  }}
                >
                  <div className="shape-clip">
                    {url ? <img src={url} alt="" draggable="false" /> : null}
                    <span className="shape-caption">{shapeLabel(shape)}</span>
                  </div>
                  {selected ? (
                    <button
                      type="button"
                      className="shape-trash"
                      aria-label="Delete shape"
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        removeShape(shape);
                      }}
                    >
                      <TrashIcon />
                    </button>
                  ) : null}
                  {canResize ? ['nw', 'ne', 'sw', 'se'].map((handle) => (
                    <button
                      key={handle}
                      type="button"
                      className={`handle handle-${handle}`}
                      aria-label={`Resize from ${handle}`}
                      onPointerDown={(event) => onShapePointerDown(event, shape, handle)}
                    />
                  )) : null}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {imageBroken ? <p className="error-text banner-inline">The photo could not be loaded.</p> : null}
      {error ? <p className="error-text banner-inline" role="alert">{error}</p> : null}
      <div className="toolbar">
        {!sealed ? (
          <div className="add-group">
            <button type="button" className="btn" onClick={() => addShape('rect')} disabled={Boolean(busy)}>
              {busy === 'add' ? 'Adding…' : 'Add rectangle'}
            </button>
            {nested ? (
              <button type="button" className="btn btn-ghost" onClick={() => addShape('circle')} disabled={Boolean(busy)}>
                Add circle
              </button>
            ) : null}
            <div className="add-sub">
              <button type="button" className="btn btn-ghost" onClick={useEntireFrame} disabled={Boolean(busy)}>
                {busy === 'frame' ? 'Adding…' : 'Use entire frame'}
              </button>
              <button type="button" className="btn" onClick={confirmSave} disabled={Boolean(busy) || shapes.length === 0}>
                {busy === 'save' ? 'Saving…' : 'Confirm and save'}
              </button>
            </div>
          </div>
        ) : (
          <p className="hint toolbar-note">Saved. Double-click a shape to go inside it.</p>
        )}
        <div className="zoom-group">
          <span className="meta">{shapes.length}/{cfg.max_shapes}</span>
          <button type="button" className="btn btn-ghost icon-btn" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.2)}>−</button>
          <button type="button" className="btn btn-ghost" onClick={center}>Reset</button>
          <button type="button" className="btn btn-ghost icon-btn" aria-label="Zoom in" onClick={() => zoomBy(1.2)}>+</button>
        </div>
      </div>
      {menu && menuShape ? (
        <ContextMenu x={menu.x} y={menu.y} anchor={menu.anchor} onClose={() => setMenu(null)} label="Shape">
          {nested ? (
            <button type="button" role="menuitem" onClick={() => switchType(menuShape)} disabled={busy === 'type'}>
              {menuShape.type === 'text' ? 'Switch to photo' : 'Switch to text'}
            </button>
          ) : null}
          <button type="button" role="menuitem" onClick={() => descend(menuShape)}>Edit</button>
          {nested ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenu(null);
                openObject({ kind: 'shape', id: menuShape.id, projectId });
              }}
            >
              Choose another variant
            </button>
          ) : null}
        </ContextMenu>
      ) : null}
    </div>
  );
}

function shapeLabel(shape) {
  if (shape.kind === 'full_frame') return 'Frame';
  if (shape.kind === 'circle') return shape.type === 'text' ? 'Text' : 'Photo';
  if (shape.type === 'text') return 'Text';
  if (shape.type === 'photo') return 'Photo';
  return 'Rectangle';
}
