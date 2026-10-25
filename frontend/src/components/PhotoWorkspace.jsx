import { useRef, useState } from 'react';
import { shapeSource } from '../api';
import { useWorkspace } from '../workspace';
import GenerationForm from './GenerationForm';
import MaskPainter from './MaskPainter';

function initialPhotoMode(mode) {
  return mode === 'mask_fill' || mode === 'customize' ? mode : 'customize';
}

export default function PhotoWorkspace({ projectId, shapeId, preset = null, mode: initialMode }) {
  const { openObject } = useWorkspace();
  const [mode, setMode] = useState(() => initialPhotoMode(initialMode ?? preset?.mode));
  const maskRef = useRef(null);

  return (
    <div className="view-frame">
      <div className="view-body">
        <div className="sheet sheet-wide">
          <MaskPainter
            ref={maskRef}
            imageUrl={shapeSource(shapeId, false)}
            active={mode === 'mask_fill'}
            maskUrl={preset?.mask_url || ''}
          />
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
          <GenerationForm mode={mode} projectId={projectId} shapeId={shapeId} maskRef={maskRef} preset={preset} />
        </div>
      </div>
    </div>
  );
}
