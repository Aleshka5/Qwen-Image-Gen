import { useEffect, useRef } from 'react';

export default function Dialog({ title, message, imageUrl, children, confirmLabel, danger, onConfirm, onCancel }) {
  const ref = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    const node = ref.current;
    const focusTarget = node?.querySelector('[data-autofocus]') || node?.querySelector('button');
    focusTarget?.focus();
    function onKey(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel?.();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (previous && previous.focus) previous.focus();
    };
  }, [onCancel]);

  return (
    <div className="dialog-backdrop" onPointerDown={onCancel}>
      <div
        className={`dialog ${imageUrl || children ? 'dialog-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        ref={ref}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <h2 id="dialog-title">{title}</h2>
        {message ? <p className="dialog-copy">{message}</p> : null}
        {imageUrl ? <img className="render-preview" src={imageUrl} alt="Render" /> : null}
        {children}
        <div className="dialog-actions">
          {onCancel ? (
            <button type="button" className="btn btn-ghost" onClick={onCancel}>
              {onConfirm ? 'Cancel' : 'Close'}
            </button>
          ) : null}
          {onConfirm ? (
            <button
              type="button"
              className={danger ? 'btn btn-danger' : 'btn'}
              data-autofocus="true"
              onClick={onConfirm}
            >
              {confirmLabel || 'Confirm'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
