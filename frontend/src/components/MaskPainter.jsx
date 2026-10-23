import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';

const MaskPainter = forwardRef(function MaskPainter({ imageUrl, active }, ref) {
  const imgRef = useRef(null);
  const maskRef = useRef(null);
  const overRef = useRef(null);
  const drawing = useRef(null);
  const history = useRef([]);
  const [tool, setTool] = useState('brush');
  const [size, setSize] = useState(36);
  const [ready, setReady] = useState(false);

  useImperativeHandle(ref, () => ({
    hasPaint() {
      const canvas = maskRef.current;
      if (!canvas) return false;
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < data.length; i += 32) {
        if (data[i] > 200) return true;
      }
      return false;
    },
    exportFile() {
      const canvas = maskRef.current;
      return new Promise((resolve, reject) => {
        if (!canvas) {
          reject(new Error('The mask is not ready.'));
          return;
        }
        canvas.toBlob((blob) => {
          if (!blob) {
            reject(new Error('Could not build the mask.'));
            return;
          }
          resolve(new File([blob], 'mask.png', { type: 'image/png' }));
        }, 'image/png');
      });
    },
  }));

  function prepare() {
    const img = imgRef.current;
    const mask = maskRef.current;
    const over = overRef.current;
    if (!img || !mask || !over || !img.naturalWidth) return;
    mask.width = img.naturalWidth;
    mask.height = img.naturalHeight;
    over.width = img.naturalWidth;
    over.height = img.naturalHeight;
    mask.getContext('2d').fillStyle = '#000';
    mask.getContext('2d').fillRect(0, 0, mask.width, mask.height);
    over.getContext('2d').clearRect(0, 0, over.width, over.height);
    history.current = [];
    setReady(true);
  }

  useEffect(() => {
    setReady(false);
  }, [imageUrl]);

  function point(event) {
    const canvas = overRef.current;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) * canvas.width) / rect.width,
      y: ((event.clientY - rect.top) * canvas.height) / rect.height,
    };
  }

  function radius() {
    const canvas = overRef.current;
    const rect = canvas.getBoundingClientRect();
    return Math.max(1, (size * canvas.width) / rect.width);
  }

  function stroke(from, to, eraser) {
    const mask = maskRef.current.getContext('2d');
    const over = overRef.current.getContext('2d');
    const width = radius();
    mask.lineWidth = width;
    mask.lineCap = 'round';
    mask.lineJoin = 'round';
    mask.strokeStyle = eraser ? '#000' : '#fff';
    mask.beginPath();
    mask.moveTo(from.x, from.y);
    mask.lineTo(to.x, to.y);
    mask.stroke();

    over.lineWidth = width;
    over.lineCap = 'round';
    over.lineJoin = 'round';
    over.globalCompositeOperation = eraser ? 'destination-out' : 'source-over';
    over.strokeStyle = 'rgba(255, 253, 248, 0.78)';
    over.beginPath();
    over.moveTo(from.x, from.y);
    over.lineTo(to.x, to.y);
    over.stroke();
    over.globalCompositeOperation = 'source-over';
  }

  function snapshot() {
    const canvas = maskRef.current;
    if (!canvas) return;
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
    history.current.push(data);
    if (history.current.length > 20) history.current.shift();
  }

  function rebuildOverlay() {
    const mask = maskRef.current;
    const over = overRef.current;
    const pixels = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height);
    const out = over.getContext('2d').createImageData(mask.width, mask.height);
    for (let i = 0; i < pixels.data.length; i += 4) {
      if (pixels.data[i] > 128) {
        out.data[i] = 255;
        out.data[i + 1] = 253;
        out.data[i + 2] = 248;
        out.data[i + 3] = 190;
      }
    }
    over.getContext('2d').putImageData(out, 0, 0);
  }

  function onPointerDown(event) {
    if (!active || !ready || event.button > 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    snapshot();
    const start = point(event);
    drawing.current = start;
    stroke(start, start, tool === 'eraser');
  }

  function onPointerMove(event) {
    if (!drawing.current) return;
    const next = point(event);
    stroke(drawing.current, next, tool === 'eraser');
    drawing.current = next;
  }

  function endDraw() {
    drawing.current = null;
  }

  function undo() {
    const prev = history.current.pop();
    if (!prev || !maskRef.current) return;
    maskRef.current.getContext('2d').putImageData(prev, 0, 0);
    rebuildOverlay();
  }

  function clearMask() {
    if (!maskRef.current) return;
    snapshot();
    const ctx = maskRef.current.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, maskRef.current.width, maskRef.current.height);
    overRef.current.getContext('2d').clearRect(0, 0, overRef.current.width, overRef.current.height);
  }

  return (
    <div className="painter">
      <div className="crop-frame">
        <img ref={imgRef} src={imageUrl} alt="Crop" onLoad={prepare} />
        <canvas
          ref={overRef}
          className={active ? 'mask-overlay' : 'mask-overlay is-hidden'}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDraw}
          onPointerCancel={endDraw}
        />
      </div>
      <canvas ref={maskRef} className="sr-only" aria-hidden="true" />
      {active ? (
        <div className="painter-tools">
          <button type="button" className={tool === 'brush' ? 'btn' : 'btn btn-ghost'} onClick={() => setTool('brush')} aria-pressed={tool === 'brush'}>
            Brush
          </button>
          <button type="button" className={tool === 'eraser' ? 'btn' : 'btn btn-ghost'} onClick={() => setTool('eraser')} aria-pressed={tool === 'eraser'}>
            Eraser
          </button>
          <label className="size-label">
            Size
            <input type="range" min="8" max="160" value={size} onChange={(event) => setSize(Number(event.target.value))} />
          </label>
          <button type="button" className="btn btn-ghost" onClick={undo}>Undo</button>
          <button type="button" className="btn btn-ghost" onClick={clearMask}>Clear</button>
        </div>
      ) : null}
      {active ? <p className="hint">White paint is the area to fill.</p> : null}
    </div>
  );
});

export default MaskPainter;
