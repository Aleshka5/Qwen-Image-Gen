import { useRef } from 'react';

export default function ReferenceList({ files, onChange, max, disabled }) {
  const inputRef = useRef(null);
  const dragIndex = useRef(null);

  function addFiles(list) {
    const next = files.slice();
    for (const file of list) {
      if (next.length >= max) break;
      if (file.type && !file.type.startsWith('image/')) continue;
      next.push({
        id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`,
        file,
        url: URL.createObjectURL(file),
      });
    }
    onChange(next);
  }

  function remove(id) {
    const item = files.find((file) => file.id === id);
    if (item) URL.revokeObjectURL(item.url);
    onChange(files.filter((file) => file.id !== id));
  }

  function move(index, dir) {
    const target = index + dir;
    if (target < 0 || target >= files.length) return;
    const next = files.slice();
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    onChange(next);
  }

  function dropOn(index) {
    const from = dragIndex.current;
    dragIndex.current = null;
    if (from == null || from === index) return;
    const next = files.slice();
    const [item] = next.splice(from, 1);
    next.splice(index, 0, item);
    onChange(next);
  }

  return (
    <div className="refs">
      <div className="refs-head">
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => inputRef.current?.click()}
          disabled={disabled || files.length >= max}
        >
          Add photos
        </button>
        <span className="meta">{files.length} / {max}</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(event) => {
          addFiles(event.target.files || []);
          event.target.value = '';
        }}
      />
      {files.length === 0 ? <p className="hint">Optional. Order is the reference order in the prompt.</p> : null}
      <ul className="ref-list">
        {files.map((item, index) => (
          <li
            key={item.id}
            className="ref-item"
            draggable
            onDragStart={() => {
              dragIndex.current = index;
            }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => dropOn(index)}
          >
            <img src={item.url} alt="" />
            <div className="ref-copy">
              <span className="ref-index">{index + 1}</span>
              <span className="ref-name">{item.file.name}</span>
              <div className="ref-actions">
              <button type="button" aria-label="Move earlier" onClick={() => move(index, -1)} disabled={index === 0}>
                ↑
              </button>
              <button
                type="button"
                aria-label="Move later"
                onClick={() => move(index, 1)}
                disabled={index === files.length - 1}
              >
                ↓
              </button>
              <button type="button" aria-label={`Remove ${item.file.name}`} onClick={() => remove(item.id)}>
                ×
              </button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
