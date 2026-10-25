import { useWorkspace } from '../workspace';
import GenerationForm from './GenerationForm';

export default function GenerateWorkspace({ projectId, preset = null }) {
  const { openObject } = useWorkspace();
  return (
    <div className="view-frame">
      <div className="view-body">
        <div className="sheet">
          <p className="hint">Scratch project. There is no geometry on this page.</p>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => openObject({ kind: 'project', id: projectId })}
          >
            Choose another variant
          </button>
          <GenerationForm mode="generate" projectId={projectId} showSize preset={preset} />
        </div>
      </div>
    </div>
  );
}
