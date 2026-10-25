import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export default function ContextMenu({ x, y, onClose, children, label, anchor = 'top-left' }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: x, top: y, ready: false });

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const margin = 8;
    let left = anchor === 'top-right' ? x - rect.width : x;
    let top = y;
    if (left + rect.width > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - rect.width - margin);
    }
    if (top + rect.height > window.innerHeight - margin) {
      top = Math.max(margin, window.innerHeight - rect.height - margin);
    }
    if (left < margin) left = margin;
    if (top < margin) top = margin;
    setPos({ left, top, ready: true });
  }, [x, y, children, anchor]);

  useEffect(() => {
    function onKey(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    }
    function onPointer(event) {
      if (!ref.current?.contains(event.target)) onClose();
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer, true);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer, true);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={ref}
      className={`menu ${pos.ready ? 'is-ready' : ''}`}
      role="menu"
      aria-label={label || 'Actions'}
      style={{ left: pos.left, top: pos.top }}
    >
      {children}
    </div>,
    document.body,
  );
}
