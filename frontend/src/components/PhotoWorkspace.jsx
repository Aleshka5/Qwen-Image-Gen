import { useRef, useState } from 'react';
import { shapeSource } from '../api';
import { useWorkspace } from '../workspace';
import GenerationForm from './GenerationForm';
import MaskPainter from './MaskPainter';

export default function PhotoWorkspace({ projectId, shapeId }) {
  const { openObject } = useWorkspace();
  const [mode, setMode] = useState('customize');
  const maskRef = useRef(null);

  return (
    <div className="view-frame">
      <div className="view-body">
        <div className="sheet sheet-wide">
          <MaskPainter ref={maskRef} imageUrl={shapeSource(shapeId, false)} active={mode === 'mask_fill'} />
          <div className="segment" role="group" aria-label="Edit mode">
            <button type="button" aria-pressed={mode === 'customize'} onClick={() => setMode('customize')}>
              Customisation
            </button>
            <button type="button" aria-pressed={mode === 'mask_fill'} onClick={() => setMode('mask_fill')}>
              Mask fill
            </button>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => openObject({ kind: 'shape', id: shapeId, projectId })}
          >
            Choose another variant
          </button>
          <GenerationForm mode={mode} projectId={projectId} shapeId={shapeId} maskRef={maskRef} />
        </div>
      </div>
    </div>
  );
}
