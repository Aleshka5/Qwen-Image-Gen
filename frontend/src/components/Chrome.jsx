import { PanelIcon, ProjectsIcon } from './Icons';
import { useWorkspace } from '../workspace';

export default function WorkChrome() {
  const {
    stack,
    projects,
    push,
    pop,
    leftOpen,
    rightOpen,
    setLeftOpen,
    setRightOpen,
    renderShot,
    rendering,
  } = useWorkspace();
  const view = stack[stack.length - 1];
  const project = projects.find((item) => item.id === view?.projectId);
  const title = titleFor(view, project);
  const showUp = view?.type !== 'home';
  const render = renderFor(view);

  return (
    <div className="chrome">
      <button
        type="button"
        className="edge-btn edge-left"
        aria-expanded={leftOpen}
        aria-controls="project-sidebar"
        onClick={() => setLeftOpen((open) => !open)}
      >
        <ProjectsIcon />
        <span className="sr-only">{leftOpen ? 'Close projects' : 'Open projects'}</span>
      </button>
      {showUp ? (
        <button type="button" className="btn btn-ghost" onClick={pop}>
          Up
        </button>
      ) : null}
      <h1>{title}</h1>
      <div className="chrome-actions">
        {render ? (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={rendering}
            onClick={() => renderShot(render.scope, render.ids)}
          >
            {rendering ? 'Rendering…' : 'Render'}
          </button>
        ) : null}
        {view?.type === 'settings' ? null : (
          <button type="button" className="btn btn-ghost" onClick={() => push({ type: 'settings' })}>
            Settings
          </button>
        )}
      </div>
      <button
        type="button"
        className="edge-btn edge-right"
        aria-expanded={rightOpen}
        aria-controls="library-sidebar"
        onClick={() => setRightOpen((open) => !open)}
      >
        <PanelIcon />
        <span className="sr-only">{rightOpen ? 'Close the side panel' : 'Open the side panel'}</span>
      </button>
    </div>
  );
}

function titleFor(view, project) {
  if (!view || view.type === 'home') return 'Home';
  if (view.type === 'settings') return 'Settings';
  if (view.type === 'editor') return view.parentId ? 'Part' : (project?.name || 'Project');
  if (view.type === 'photo') return 'Photo';
  if (view.type === 'text') return 'Text';
  if (view.type === 'generate') return project?.name || 'Generate';
  return 'Workspace';
}

function renderFor(view) {
  if (!view) return null;
  if (view.type === 'editor' && !view.parentId) return { scope: 'project', ids: { projectId: view.projectId } };
  if (view.type === 'editor' && view.parentId) return { scope: 'shape', ids: { shapeId: view.parentId } };
  if (view.type === 'photo' || view.type === 'text') return { scope: 'shape', ids: { shapeId: view.shapeId } };
  if (view.type === 'generate') return { scope: 'project', ids: { projectId: view.projectId } };
  return null;
}
