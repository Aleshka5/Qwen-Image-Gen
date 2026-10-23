import { useRef, useState } from 'react';
import { api, asObject } from '../api';
import { useWorkspace } from '../workspace';
import ContextMenu from './ContextMenu';

export default function Home() {
  const { push, notify, refreshProjects } = useWorkspace();
  const [menu, setMenu] = useState(null);
  const [busy, setBusy] = useState('');
  const fileRef = useRef(null);
  const plusRef = useRef(null);

  function toggleMenu() {
    if (menu) {
      setMenu(null);
      return;
    }
    const rect = plusRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMenu({ x: rect.left + rect.width / 2 - 110, y: rect.bottom + 8 });
  }

  async function create(kind, file) {
    setBusy(kind);
    setMenu(null);
    try {
      const form = new FormData();
      form.set('kind', kind);
      if (file) form.set('file', file);
      const data = await api.createProject(form);
      const project = asObject(data, 'project');
      if (!project?.id) throw new Error('The project was created without an id.');
      await refreshProjects();
      if (kind === 'scratch') push({ type: 'generate', projectId: project.id });
      else push({ type: 'editor', projectId: project.id, parentId: null });
    } catch (err) {
      notify(err.message);
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="view-frame">
      <div className="view-body home-body">
        <div className="home-center">
          <p className="kicker">Workspace</p>
          <button
            ref={plusRef}
            type="button"
            className="plus"
            aria-haspopup="menu"
            aria-expanded={menu ? true : false}
            onClick={toggleMenu}
            disabled={Boolean(busy)}
          >
            <span aria-hidden="true">{busy ? '…' : '+'}</span>
            <span className="sr-only">{busy ? 'Creating a project' : 'Start something'}</span>
          </button>
          <p className="home-caption">Upload a photo or generate from scratch.</p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            if (file.type && !file.type.startsWith('image/')) {
              notify('Choose an image file.');
              return;
            }
            create('photo', file);
          }}
        />
      </div>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} label="Start">
          <button
            type="button"
            role="menuitem"
            disabled={Boolean(busy)}
            onClick={() => {
              setMenu(null);
              fileRef.current?.click();
            }}
          >
            Upload a photo
          </button>
          <button type="button" role="menuitem" onClick={() => create('scratch')} disabled={Boolean(busy)}>
            {busy === 'scratch' ? 'Creating…' : 'Generate from scratch'}
          </button>
        </ContextMenu>
      ) : null}
    </div>
  );
}
