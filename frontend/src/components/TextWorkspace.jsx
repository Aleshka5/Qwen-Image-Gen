import { shapeSource } from '../api';
import { useWorkspace } from '../workspace';

export default function TextWorkspace({ projectId, shapeId }) {
  const { openObject } = useWorkspace();
  return (
    <div className="view-frame">
      <div className="view-body">
        <div className="sheet">
          <img className="crop-still" src={shapeSource(shapeId, false)} alt="Original crop" />
          <p className="callout">Text editing arrives with OCR later. This place is reserved.</p>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => openObject({ kind: 'shape', id: shapeId, projectId })}
          >
            Choose another variant
          </button>
        </div>
      </div>
    </div>
  );
}
